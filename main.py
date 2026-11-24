
import os
import asyncio
import random
import logging
import html

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.error import TelegramError

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

games = {}


# =====================================
# СОСТАВ РОЛЕЙ
# =====================================

def build_roles(count):
    if count < 6:
        return ["Мафия"] + ["Мирный житель"] * (count - 1)

    mafia_count = 1 if count == 6 else 2 if count <= 10 else 3

    roles = ["Мафия"] * mafia_count

    if count >= 10:
        roles[0] = "Дон мафии"

    roles += ["Комиссар", "Доктор"]

    if count == 9 or count >= 12:
        roles.append("Маньяк")

    while len(roles) < count:
        roles.append("Мирный житель")

    return roles[:count]


# =====================================
# СОЗДАНИЕ ИГРЫ
# =====================================

async def mafia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if chat.type == "private":
        await update.message.reply_text(
            "Добавь меня в групповой чат, чтобы играть в Мафию!"
        )
        return

    old_game = games.get(chat.id)

    if old_game and old_game["phase"] != "finished":
        await update.message.reply_text("В этом чате уже идёт игра!")
        return

    games[chat.id] = {
        "phase": "lobby",
        "host": update.effective_user.id,
        "players": {},
        "round": 0,
        "votes": {},
        "night_actions": {},
        "checks": {},
        "dead": []
    }

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎭 Вступить в игру", callback_data="join")],
        [InlineKeyboardButton("▶️ Начать игру", callback_data="start")]
    ])

    await update.message.reply_text(
        "🎭 <b>МАФИЯ</b>\n\n"
        "Новая игра создана!\n\n"
        "Нажимайте кнопку, чтобы присоединиться.\n"
        "Минимум игроков: 3",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# =====================================
# РЕГИСТРАЦИЯ ИГРОКОВ
# =====================================

async def join_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    user = query.from_user
    game = games.get(chat_id)

    if not game or game["phase"] != "lobby":
        await query.answer("Регистрация закрыта!", show_alert=True)
        return

    if user.id in game["players"]:
        await query.answer("Ты уже в игре!", show_alert=True)
        return

    try:
        await context.bot.send_message(
            user.id,
            "🎭 Ты присоединился к игре в Мафию!\n"
            "Ожидай начала игры."
        )
    except TelegramError:
        await query.answer(
            "Сначала открой бота и нажми /start в личных сообщениях!",
            show_alert=True
        )
        return

    game["players"][user.id] = {
        "name": user.full_name,
        "role": None,
        "alive": True
    }

    await query.edit_message_text(
        "🎭 <b>МАФИЯ — регистрация</b>\n\n"
        + "\n".join(
            f"{i}. {html.escape(p['name'])}"
            for i, p in enumerate(game["players"].values(), 1)
        )
        + f"\n\n👥 Игроков: {len(game['players'])}\n"
        "Минимум: 3",
        reply_markup=query.message.reply_markup,
        parse_mode="HTML"
    )


# =====================================
# ЗАПУСК ИГРЫ
# =====================================

async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    game = games.get(chat_id)

    if not game or game["phase"] != "lobby":
        return

    if len(game["players"]) < 3:
        await query.answer(
            "Нужно минимум 3 игрока!",
            show_alert=True
        )
        return

    players = list(game["players"].keys())
    roles = build_roles(len(players))
    random.shuffle(roles)

    for user_id, role in zip(players, roles):
        game["players"][user_id]["role"] = role

        try:
            await context.bot.send_message(
                user_id,
                f"🎭 <b>ТВОЯ РОЛЬ: {html.escape(role.upper())}</b>\n\n"
                + get_role_description(role),
                parse_mode="HTML"
            )
        except TelegramError:
            await context.bot.send_message(
                chat_id,
                f"Не удалось отправить роль игроку "
                f"{html.escape(game['players'][user_id]['name'])}."
            )
            return

    game["phase"] = "night"

    await context.bot.send_message(
        chat_id,
        "🌙 <b>Игра началась!</b>\n\n"
        "Все получили свои роли.\n"
        "Город засыпает...",
        parse_mode="HTML"
    )

    await start_night(chat_id, context)


def get_role_description(role):
    descriptions = {
        "Мафия": "🔪 Ты состоишь в мафии. Выбирай жертву ночью.",
        "Дон мафии": "🔪 Ты глава мафии. Выбирай жертву вместе с мафией.",
        "Комиссар": "🕵️ Проверяй игроков ночью, чтобы найти мафию.",
        "Доктор": "💉 Выбирай игрока, которого хочешь спасти ночью.",
        "Маньяк": "🔪 Ты действуешь самостоятельно. Выбирай жертву ночью.",
        "Мирный житель": "🏡 Найди мафию днём и голосуй против неё."
    }
    return descriptions.get(role, "")


# =====================================
# НОЧЬ
# =====================================

async def start_night(chat_id, context):
    game = games[chat_id]

    game["phase"] = "night"
    game["round"] += 1
    game["night_actions"] = {}
    game["checks"] = {}

    await context.bot.send_message(
        chat_id,
        f"🌙 <b>НОЧЬ {game['round']}</b>\n\n"
        "Город засыпает. Специальные роли делают свой ход.",
        parse_mode="HTML"
    )

    for user_id, player in game["players"].items():
        if not player["alive"]:
            continue

        role = player["role"]

        if role in ("Мафия", "Дон мафии"):
            await send_night_action(
                user_id, chat_id, game, context,
                "🔪 Выберите жертву мафии:",
                exclude_mafia=True
            )

        elif role == "Доктор":
            await send_night_action(
                user_id, chat_id, game, context,
                "💉 Кого будете лечить?"
            )

        elif role == "Комиссар":
            await send_night_action(
                user_id, chat_id, game, context,
                "🕵️ Кого проверим?"
            )

        elif role == "Маньяк":
            await send_night_action(
                user_id, chat_id, game, context,
                "🔪 Выберите свою жертву."
            )

    await check_night_complete(chat_id, context)


async def send_night_action(
    user_id, chat_id, game, context, message,
    exclude_mafia=False
):
    keyboard = []

    for target_id, player in game["players"].items():
        if not player["alive"] or target_id == user_id:
            continue

        if exclude_mafia and player["role"] in ("Мафия", "Дон мафии"):
            continue

        keyboard.append([
            InlineKeyboardButton(
                player["name"],
                callback_data=f"n:{chat_id}:{target_id}"
            )
        ])

    if keyboard:
        await context.bot.send_message(
            user_id,
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def night_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, chat_id, target_id = query.data.split(":")
    chat_id = int(chat_id)
    target_id = int(target_id)

    game = games.get(chat_id)
    user_id = query.from_user.id

    if not game or game["phase"] != "night":
        await query.answer("Ночь уже закончилась!", show_alert=True)
        return

    player = game["players"].get(user_id)
    target = game["players"].get(target_id)

    if not player or not player["alive"] or not target or not target["alive"]:
        await query.answer("Этот ход уже недоступен!", show_alert=True)
        return

    if user_id in game["night_actions"]:
        await query.answer("Ты уже сделал ход!", show_alert=True)
        return

    role = player["role"]

    if role not in ("Мафия", "Дон мафии", "Доктор", "Комиссар", "Маньяк"):
        return

    if role in ("Мафия", "Дон мафии") and target["role"] in ("Мафия", "Дон мафии"):
        await query.answer("Нельзя выбирать своего!", show_alert=True)
        return

    game["night_actions"][user_id] = target_id

    if role == "Комиссар":
        is_mafia = target["role"] in ("Мафия", "Дон мафии")
        result = "🔴 Это мафия!" if is_mafia else "🟢 Это не мафия!"

        await context.bot.send_message(
            user_id,
            f"🕵️ Результат проверки:\n{html.escape(target['name'])}\n\n{result}"
        )

    await query.edit_message_text("✅ Твой ночной ход принят!")

    await check_night_complete(chat_id, context)


# =====================================
# ЗАВЕРШЕНИЕ НОЧИ
# =====================================

async def check_night_complete(chat_id, context):
    game = games[chat_id]

    required = [
        user_id
        for user_id, player in game["players"].items()
        if player["alive"] and player["role"] in (
            "Мафия", "Дон мафии", "Доктор", "Комиссар", "Маньяк"
        )
    ]

    if not all(uid in game["night_actions"] for uid in required):
        return

    actions = game["night_actions"]

    mafia_votes = [
        actions[uid]
        for uid, p in game["players"].items()
        if p["alive"] and p["role"] in ("Мафия", "Дон мафии")
        and uid in actions
    ]

    doctor_target = next(
        (actions[uid] for uid, p in game["players"].items()
         if p["alive"] and p["role"] == "Доктор" and uid in actions),
        None
    )

    maniac_target = next(
        (actions[uid] for uid, p in game["players"].items()
         if p["alive"] and p["role"] == "Маньяк" and uid in actions),
        None
    )

    mafia_target = None

    if mafia_votes:
        counts = {}
        for target in mafia_votes:
            counts[target] = counts.get(target, 0) + 1

        maximum = max(counts.values())
        winners = [uid for uid, count in counts.items() if count == maximum]

        if len(winners) == 1:
            mafia_target = winners[0]

    killed = []

    for target in [mafia_target, maniac_target]:
        if target and target != doctor_target and target not in killed:
            killed.append(target)

    for target_id in killed:
        if target_id in game["players"]:
            game["players"][target_id]["alive"] = False
            game["dead"].append(target_id)

    if killed:
        names = "\n".join(
            "💀 " + html.escape(game["players"][uid]["name"])
            for uid in killed
        )
    else:
        names = "Сегодня ночью никто не погиб!"

    await context.bot.send_message(
        chat_id,
        f"☀️ <b>НАСТУПИЛО УТРО</b>\n\n{names}",
        parse_mode="HTML"
    )

    if await check_winner(chat_id, context):
        return

    await start_voting(chat_id, context)


# =====================================
# ДНЕВНОЕ ГОЛОСОВАНИЕ
# =====================================

async def start_voting(chat_id, context):
    game = games[chat_id]

    game["phase"] = "voting"
    game["votes"] = {}

    keyboard = []

    for user_id, player in game["players"].items():
        if player["alive"]:
            keyboard.append([
                InlineKeyboardButton(
                    f"🗳 {player['name']}",
                    callback_data=f"v:{chat_id}:{user_id}"
                )
            ])

    await context.bot.send_message(
        chat_id,
        "🗳 <b>ДЕНЬ. ГОЛОСОВАНИЕ</b>\n\n"
        "Обсудите, кто может быть мафией.\n"
        "Затем каждый живой игрок выбирает подозреваемого.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, chat_id, target_id = query.data.split(":")
    chat_id = int(chat_id)
    target_id = int(target_id)

    game = games.get(chat_id)
    user_id = query.from_user.id

    if not game or game["phase"] != "voting":
        return

    voter = game["players"].get(user_id)
    target = game["players"].get(target_id)

    if not voter or not voter["alive"]:
        await query.answer("Ты выбыл из игры!", show_alert=True)
        return

    if not target or not target["alive"]:
        return

    if user_id in game["votes"]:
        await query.answer("Ты уже проголосовал!", show_alert=True)
        return

    game["votes"][user_id] = target_id

    await query.answer("Голос принят!")

    alive = [
        uid for uid, p in game["players"].items() if p["alive"]
    ]

    await context.bot.send_message(
        chat_id,
        f"🗳 Голос принят: {len(game['votes'])}/{len(alive)}"
    )

    if len(game["votes"]) == len(alive):
        await finish_voting(chat_id, context)


async def finish_voting(chat_id, context):
    game = games[chat_id]

    counts = {}

    for target in game["votes"].values():
        counts[target] = counts.get(target, 0) + 1

    maximum = max(counts.values())
    winners = [uid for uid, count in counts.items() if count == maximum]

    if len(winners) == 1:
        target_id = winners[0]
        game["players"][target_id]["alive"] = False
        game["dead"].append(target_id)

        player = game["players"][target_id]

        await context.bot.send_message(
            chat_id,
            f"⚖️ Большинство проголосовало против "
            f"<b>{html.escape(player['name'])}</b>.\n\n"
            f"Его роль: <b>{html.escape(player['role'])}</b>.",
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id,
            "⚖️ Ничья! Никто не покидает игру."
        )

    if await check_winner(chat_id, context):
        return

    await start_night(chat_id, context)


# =====================================
# ПРОВЕРКА ПОБЕДИТЕЛЯ
# =====================================

async def check_winner(chat_id, context):
    game = games[chat_id]

    alive = [
        p for p in game["players"].values() if p["alive"]
    ]

    mafia = sum(
        p["role"] in ("Мафия", "Дон мафии") for p in alive
    )

    maniac = sum(p["role"] == "Маньяк" for p in alive)

    if mafia == 0 and maniac == 0:
        result = "🏆 ПОБЕДА МИРНЫХ ЖИТЕЛЕЙ!"
    elif mafia >= len(alive) - mafia:
        result = "🔪 ПОБЕДА МАФИИ!"
    elif len(alive) == 1 and maniac == 1:
        result = "🔪 ПОБЕДА МАНЬЯКА!"
    else:
        return False

    game["phase"] = "finished"

    details = "\n".join(
        f"{html.escape(p['name'])} — "
        f"{html.escape(p['role'])}"
        + (" 💀" if not p["alive"] else "")
        for p in game["players"].values()
    )

    await context.bot.send_message(
        chat_id,
        f"🎉 <b>{result}</b>\n\n"
        f"<b>Результаты игры:</b>\n{details}\n\n"
        "Для новой игры напишите /mafia",
        parse_mode="HTML"
    )

    return True


# =====================================
# КОМАНДЫ
# =====================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎭 Привет! Я бот для игры в Мафию.\n\n"
        "Добавь меня в группу и напиши /mafia.\n\n"
        "Важно: перед игрой нажми /start здесь, "
        "чтобы я мог отправлять тебе секретные роли."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = games.get(chat_id)

    if not game:
        await update.message.reply_text("Сейчас нет активной игры.")
        return

    alive = sum(p["alive"] for p in game["players"].values())

    await update.message.reply_text(
        f"🎭 Игроков: {len(game['players'])}\n"
        f"👥 В игре: {alive}\n"
        f"🌙 Раунд: {game['round']}\n"
        f"📌 Этап: {game['phase']}"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = games.get(chat_id)

    if not game or game["phase"] == "finished":
        await update.message.reply_text("Нет активной игры.")
        return

    game["phase"] = "finished"

    await update.message.reply_text("🛑 Игра отменена!")


# =====================================
# ВЕБ-СЕРВЕР ДЛЯ RENDER
# =====================================

async def health_server(reader, writer):
    try:
        await reader.read(1024)

        response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: 2\r\n"
            "Connection: close\r\n\r\n"
            "OK"
        )

        writer.write(response.encode())
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def post_init(application):
    server = await asyncio.start_server(
        health_server,
        "0.0.0.0",
        PORT
    )

    application.bot_data["health_server"] = server

    logging.info("Health server started on port %s", PORT)


async def post_shutdown(application):
    server = application.bot_data.get("health_server")

    if server:
        server.close()
        await server.wait_closed()


# =====================================
# ЗАПУСК БОТА
# =====================================

def main():
    if not TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения!")

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mafia", mafia))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(CallbackQueryHandler(join_game, pattern="^join$"))
    app.add_handler(CallbackQueryHandler(start_game, pattern="^start$"))
    app.add_handler(CallbackQueryHandler(night_action, pattern=r"^n:"))
    app.add_handler(CallbackQueryHandler(vote, pattern=r"^v:"))

    logging.info("Mafia bot is starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
