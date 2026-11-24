
import os
import random
import logging
import html

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

games = {}


# ==========================================
# ИМЕНА ИГРОКОВ
# ==========================================

def player_name(user):
    if user.username:
        return f"@{user.username}"
    return user.full_name


def alive_players(game):
    return {
        uid: player
        for uid, player in game["players"].items()
        if player["alive"]
    }


# ==========================================
# РАСПРЕДЕЛЕНИЕ РОЛЕЙ
# ==========================================

def build_roles(count):
    if count < 6:
        return ["Мафия"] + ["Мирный житель"] * (count - 1)

    if count <= 6:
        mafia_count = 1
    elif count <= 10:
        mafia_count = 2
    elif count <= 14:
        mafia_count = 3
    else:
        mafia_count = max(4, count // 4)

    roles = ["Мафия"] * mafia_count

    if count >= 10:
        roles[0] = "Дон мафии"

    roles.extend(["Комиссар", "Доктор"])

    if count == 9 or count >= 12:
        roles.append("Маньяк")

    roles.extend(
        ["Мирный житель"] * (count - len(roles))
    )

    random.shuffle(roles)
    return roles


ROLE_INFO = {
    "Мафия": "🔪 Ты мафия. Выбирай жертву вместе с командой.",
    "Дон мафии": "👑 Ты Дон. Руководи мафией и выбирай жертву.",
    "Комиссар": "🕵️ Проверяй игроков ночью.",
    "Доктор": "💉 Спасай одного игрока каждую ночь.",
    "Маньяк": "🔪 Ты действуешь самостоятельно.",
    "Мирный житель": "🏡 Найди мафию и помоги городу победить."
}


# ==========================================
# СОЗДАНИЕ ИГРЫ
# ==========================================

async def mafia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if chat.type == "private":
        await update.message.reply_text(
            "Добавь меня в группу и напиши /mafia."
        )
        return

    old = games.get(chat.id)

    if old and old["phase"] != "finished":
        await update.message.reply_text(
            "В этой группе уже идёт игра!"
        )
        return

    games[chat.id] = {
        "phase": "lobby",
        "host": update.effective_user.id,
        "players": {},
        "round": 0,
        "votes": {},
        "night_actions": {},
        "ready": set(),
        "dead": []
    }

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🎭 Вступить в игру",
            callback_data="join"
        )],
        [InlineKeyboardButton(
            "▶️ Начать игру",
            callback_data="start"
        )]
    ])

    await update.message.reply_text(
        "🎭 <b>МАФИЯ</b>\n\n"
        "Новая игра создана!\n"
        "Нажимайте кнопку, чтобы вступить.\n\n"
        "Минимум игроков: 3",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# ==========================================
# РЕГИСТРАЦИЯ
# ==========================================

async def join_game(update, context):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    user = query.from_user
    game = games.get(chat_id)

    if not game or game["phase"] != "lobby":
        await query.answer("Регистрация закрыта!", show_alert=True)
        return

    if user.id in game["players"]:
        await query.answer("Ты уже участвуешь!", show_alert=True)
        return

    try:
        await context.bot.send_message(
            user.id,
            "🎭 Ты зарегистрирован в игре Мафия!\n"
            "Ожидай начала."
        )
    except TelegramError:
        await query.answer(
            "Сначала открой бота и нажми /start!",
            show_alert=True
        )
        return

    game["players"][user.id] = {
        "name": player_name(user),
        "role": None,
        "alive": True
    }

    text = (
        "🎭 <b>МАФИЯ — регистрация</b>\n\n"
        + "\n".join(
            f"{i}. {html.escape(p['name'])}"
            for i, p in enumerate(game["players"].values(), 1)
        )
        + f"\n\n👥 Игроков: {len(game['players'])}"
    )

    await query.edit_message_text(
        text,
        reply_markup=query.message.reply_markup,
        parse_mode="HTML"
    )


# ==========================================
# ЗАПУСК ИГРЫ
# ==========================================

async def start_game(update, context):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    game = games.get(chat_id)

    if not game or game["phase"] != "lobby":
        return

    if query.from_user.id != game["host"]:
        await query.answer(
            "Начать игру может только создатель!",
            show_alert=True
        )
        return

    players = list(game["players"].keys())

    if len(players) < 3:
        await query.answer(
            "Нужно минимум 3 игрока!",
            show_alert=True
        )
        return

    # Проверяем доступность личных сообщений
    for uid in players:
        try:
            await context.bot.send_message(
                uid,
                "🎭 Игра начинается! Сейчас получишь роль."
            )
        except TelegramError:
            await context.bot.send_message(
                chat_id,
                f"Не могу написать игроку "
                f"{html.escape(game['players'][uid]['name'])}.\n"
                "Пусть он откроет бота и нажмёт /start. "
                "Игра пока не началась."
            )
            return

    roles = build_roles(len(players))

    for uid, role in zip(players, roles):
        game["players"][uid]["role"] = role

    # Раздаём роли
    for uid in players:
        role = game["players"][uid]["role"]

        try:
            await context.bot.send_message(
                uid,
                f"🎭 <b>ТВОЯ РОЛЬ: {html.escape(role.upper())}</b>\n\n"
                f"{ROLE_INFO[role]}",
                parse_mode="HTML"
            )
        except TelegramError:
            game["phase"] = "finished"
            await context.bot.send_message(
                chat_id,
                "Не удалось раздать роли. Игра остановлена."
            )
            return

    # Сообщаем мафии состав команды
    mafia_ids = [
        uid for uid in players
        if game["players"][uid]["role"] in ("Мафия", "Дон мафии")
    ]

    mafia_names = "\n".join(
        "🔪 " + html.escape(game["players"][uid]["name"])
        for uid in mafia_ids
    )

    for uid in mafia_ids:
        try:
            await context.bot.send_message(
                uid,
                "🤝 <b>ТВОЯ КОМАНДА МАФИИ</b>\n\n"
                f"{mafia_names}\n\n"
                "Ночью вы можете общаться друг с другом "
                "прямо здесь. Ваши сообщения увидит только мафия.",
                parse_mode="HTML"
            )
        except TelegramError:
            pass

    await query.edit_message_text(
        "🎭 Игра началась! Роли розданы.\n"
        "Город засыпает..."
    )

    await start_night(chat_id, context)


# ==========================================
# СЕКРЕТНЫЙ ЧАТ МАФИИ
# ==========================================

async def mafia_chat(update, context):
    user = update.effective_user
    message = update.message

    if not user or not message:
        return

    text = message.text or message.caption

    if not text:
        return

    # Находим активную игру этого игрока
    active = []

    for chat_id, game in games.items():
        player = game["players"].get(user.id)

        if (
            player
            and player["alive"]
            and player["role"] in ("Мафия", "Дон мафии")
            and game["phase"] == "night"
        ):
            active.append((chat_id, game))

    if len(active) == 0:
        return

    if len(active) > 1:
        await message.reply_text(
            "У тебя несколько активных игр. "
            "Секретный чат пока недоступен."
        )
        return

    chat_id, game = active[0]

    sender = html.escape(game["players"][user.id]["name"])
    safe_text = html.escape(text)

    mafia_ids = [
        uid for uid, p in game["players"].items()
        if p["alive"]
        and p["role"] in ("Мафия", "Дон мафии")
        and uid != user.id
    ]

    for uid in mafia_ids:
        try:
            await context.bot.send_message(
                uid,
                f"🤝 <b>{sender}:</b>\n{safe_text}",
                parse_mode="HTML"
            )
        except TelegramError:
            pass

    await message.reply_text("✅ Сообщение отправлено мафии.")


# ==========================================
# НОЧЬ
# ==========================================

async def start_night(chat_id, context):
    game = games[chat_id]

    game["phase"] = "night"
    game["round"] += 1
    game["night_actions"] = {}

    await context.bot.send_message(
        chat_id,
        f"🌙 <b>НОЧЬ {game['round']}</b>\n\n"
        "Город засыпает. Специальные роли делают свои ходы.",
        parse_mode="HTML"
    )

    for uid, player in alive_players(game).items():
        role = player["role"]

        if role in ("Мафия", "Дон мафии"):
            await send_night_action(
                uid, chat_id, game, context,
                "🔪 Выберите жертву мафии:",
                exclude_mafia=True
            )

        elif role == "Доктор":
            await send_night_action(
                uid, chat_id, game, context,
                "💉 Кого будете лечить?"
            )

        elif role == "Комиссар":
            await send_night_action(
                uid, chat_id, game, context,
                "🕵️ Кого будете проверять?"
            )

        elif role == "Маньяк":
            await send_night_action(
                uid, chat_id, game, context,
                "🔪 Выберите свою жертву."
            )

    await check_night_complete(chat_id, context)


async def send_night_action(
    uid, chat_id, game, context, title,
    exclude_mafia=False
):
    keyboard = []

    for target_id, player in alive_players(game).items():
        if target_id == uid:
            continue

        if exclude_mafia and player["role"] in (
            "Мафия", "Дон мафии"
        ):
            continue

        keyboard.append([
            InlineKeyboardButton(
                player["name"],
                callback_data=f"night|{chat_id}|{target_id}"
            )
        ])

    if keyboard:
        await context.bot.send_message(
            uid,
            title,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def night_action(update, context):
    query = update.callback_query
    await query.answer()

    _, chat, target = query.data.split("|")
    chat_id = int(chat)
    target_id = int(target)
    uid = query.from_user.id

    game = games.get(chat_id)

    if not game or game["phase"] != "night":
        await query.answer("Ночь уже закончилась!", show_alert=True)
        return

    player = game["players"].get(uid)
    victim = game["players"].get(target_id)

    if not player or not player["alive"]:
        return

    if not victim or not victim["alive"] or target_id == uid:
        await query.answer("Недопустимый выбор!", show_alert=True)
        return

    role = player["role"]

    if role not in (
        "Мафия", "Дон мафии", "Доктор", "Комиссар", "Маньяк"
    ):
        return

    if role in ("Мафия", "Дон мафии") and victim["role"] in (
        "Мафия", "Дон мафии"
    ):
        await query.answer("Нельзя выбрать сообщника!", show_alert=True)
        return

    game["night_actions"][uid] = target_id

    await query.edit_message_text("✅ Твой выбор принят!")

    if role == "Комиссар":
        is_mafia = victim["role"] in ("Мафия", "Дон мафии")
        result = "🔴 Это мафия!" if is_mafia else "🟢 Это не мафия!"

        await context.bot.send_message(
            uid,
            f"🕵️ Проверка: {html.escape(victim['name'])}\n{result}"
        )

    await check_night_complete(chat_id, context)


# ==========================================
# ЗАВЕРШЕНИЕ НОЧИ
# ==========================================

async def check_night_complete(chat_id, context):
    game = games[chat_id]

    required = [
        uid for uid, p in alive_players(game).items()
        if p["role"] in (
            "Мафия", "Дон мафии", "Доктор", "Комиссар", "Маньяк"
        )
    ]

    if not all(uid in game["night_actions"] for uid in required):
        return

    actions = game["night_actions"]

    mafia_votes = [
        actions[uid]
        for uid, p in alive_players(game).items()
        if p["role"] in ("Мафия", "Дон мафии")
    ]

    doctor_target = next(
        (
            actions[uid] for uid, p in alive_players(game).items()
            if p["role"] == "Доктор"
        ),
        None
    )

    maniac_target = next(
        (
            actions[uid] for uid, p in alive_players(game).items()
            if p["role"] == "Маньяк"
        ),
        None
    )

    mafia_target = None

    if mafia_votes:
        counts = {}

        for target in mafia_votes:
            counts[target] = counts.get(target, 0) + 1

        maximum = max(counts.values())
        winners = [
            uid for uid, count in counts.items()
            if count == maximum
        ]

        if len(winners) == 1:
            mafia_target = winners[0]

    killed = []

    for target in (mafia_target, maniac_target):
        if (
            target
            and target != doctor_target
            and target not in killed
            and target in game["players"]
            and game["players"][target]["alive"]
        ):
            game["players"][target]["alive"] = False
            game["dead"].append(target)
            killed.append(target)

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

    await start_discussion(chat_id, context)


# ==========================================
# ОБСУЖДЕНИЕ И ГОТОВНОСТЬ
# ==========================================

async def start_discussion(chat_id, context):
    game = games[chat_id]

    game["phase"] = "discussion"
    game["ready"] = set()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ Я готов к голосованию",
            callback_data=f"ready|{chat_id}"
        )]
    ])

    await context.bot.send_message(
        chat_id,
        "☀️ <b>ОБСУЖДЕНИЕ</b>\n\n"
        "Обсудите, кто может быть мафией.\n"
        "Когда закончите, каждый живой игрок нажимает кнопку.\n\n"
        "Голосование начнётся, когда будут готовы все.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def ready_vote(update, context):
    query = update.callback_query
    await query.answer()

    _, chat = query.data.split("|")
    chat_id = int(chat)
    uid = query.from_user.id

    game = games.get(chat_id)

    if not game or game["phase"] != "discussion":
        return

    player = game["players"].get(uid)

    if not player or not player["alive"]:
        await query.answer("Ты не участвуешь в обсуждении!", show_alert=True)
        return

    game["ready"].add(uid)

    alive = alive_players(game)

    await query.answer("Готовность подтверждена!")

    if len(game["ready"]) == len(alive):
        await start_voting(chat_id, context)
        return

    await query.edit_message_text(
        "☀️ <b>ОБСУЖДЕНИЕ</b>\n\n"
        f"Готовы: {len(game['ready'])}/{len(alive)}\n"
        "Ожидаем остальных игроков.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ Я готов к голосованию",
                callback_data=f"ready|{chat_id}"
            )]
        ]),
        parse_mode="HTML"
    )


# ==========================================
# ГОЛОСОВАНИЕ
# ==========================================

async def start_voting(chat_id, context):
    game = games[chat_id]

    game["phase"] = "voting"
    game["votes"] = {}

    keyboard = []

    for uid, player in alive_players(game).items():
        keyboard.append([
            InlineKeyboardButton(
                player["name"],
                callback_data=f"vote|{chat_id}|{uid}"
            )
        ])

    await context.bot.send_message(
        chat_id,
        "🗳 <b>ГОЛОСОВАНИЕ</b>\n\n"
        "Выберите подозреваемого.\n"
        "Свой голос можно менять до завершения голосования.\n"
        "Голосовать за себя нельзя.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def vote(update, context):
    query = update.callback_query
    await query.answer()

    _, chat, target = query.data.split("|")
    chat_id = int(chat)
    target_id = int(target)
    uid = query.from_user.id

    game = games.get(chat_id)

    if not game or game["phase"] != "voting":
        await query.answer("Голосование завершено!", show_alert=True)
        return

    voter = game["players"].get(uid)
    victim = game["players"].get(target_id)

    if not voter or not voter["alive"]:
        await query.answer("Ты выбыл из игры!", show_alert=True)
        return

    if not victim or not victim["alive"]:
        await query.answer("Игрок уже выбыл!", show_alert=True)
        return

    if target_id == uid:
        await query.answer(
            "Нельзя голосовать за себя!",
            show_alert=True
        )
        return

    previous = game["votes"].get(uid)
    game["votes"][uid] = target_id

    if previous:
        await query.answer("Твой голос изменён!")
    else:
        await query.answer("Голос принят!")

    alive = alive_players(game)

    if len(game["votes"]) == len(alive):
        await finish_voting(chat_id, context)
        return

    # Не раскрываем, кто за кого голосовал
    await query.edit_message_text(
        "🗳 <b>ГОЛОСОВАНИЕ</b>\n\n"
        f"Проголосовали: {len(game['votes'])}/{len(alive)}\n"
        "Голос можно изменить до окончания.",
        reply_markup=query.message.reply_markup,
        parse_mode="HTML"
    )


async def finish_voting(chat_id, context):
    game = games[chat_id]

    counts = {}

    for target in game["votes"].values():
        counts[target] = counts.get(target, 0) + 1

    maximum = max(counts.values())
    winners = [
        uid for uid, count in counts.items()
        if count == maximum
    ]

    if len(winners) == 1:
        target_id = winners[0]
        player = game["players"][target_id]

        player["alive"] = False
        game["dead"].append(target_id)

        await context.bot.send_message(
            chat_id,
            f"⚖️ Большинство решило исключить "
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


# ==========================================
# ПРОВЕРКА ПОБЕДИТЕЛЯ
# ==========================================

async def check_winner(chat_id, context):
    game = games[chat_id]
    alive = list(alive_players(game).values())

    mafia = sum(
        p["role"] in ("Мафия", "Дон мафии")
        for p in alive
    )

    maniac = sum(
        p["role"] == "Маньяк"
        for p in alive
    )

    if mafia == 0 and maniac == 0:
        result = "🏆 ПОБЕДА МИРНЫХ ЖИТЕЛЕЙ!"

    elif mafia > 0 and mafia >= len(alive) - mafia:
        result = "🔪 ПОБЕДА МАФИИ!"

    elif maniac > 0 and mafia == 0 and maniac >= len(alive) - maniac:
        result = "🔪 ПОБЕДА МАНЬЯКА!"

    else:
        return False

    game["phase"] = "finished"

    details = "\n".join(
        f"{html.escape(p['name'])} — "
        f"{html.escape(p['role'])}"
        + ("" if p["alive"] else " 💀")
        for p in game["players"].values()
    )

    await context.bot.send_message(
        chat_id,
        f"🎉 <b>{result}</b>\n\n"
        f"<b>Результаты:</b>\n{details}\n\n"
        "Для новой игры напишите /mafia",
        parse_mode="HTML"
    )

    return True


# ==========================================
# КОМАНДЫ
# ==========================================

async def start(update, context):
    await update.message.reply_text(
        "🎭 Привет! Я бот для игры в Мафию.\n\n"
        "Добавь меня в группу и напиши /mafia.\n"
        "Для участия обязательно нажми /start здесь."
    )


async def status(update, context):
    game = games.get(update.effective_chat.id)

    if not game:
        await update.message.reply_text("Активной игры нет.")
        return

    await update.message.reply_text(
        f"🎭 Игроков: {len(game['players'])}\n"
        f"👥 В игре: {len(alive_players(game))}\n"
        f"🌙 Раунд: {game['round']}\n"
        f"📌 Этап: {game['phase']}"
    )


async def cancel(update, context):
    chat_id = update.effective_chat.id
    game = games.get(chat_id)

    if not game or game["phase"] == "finished":
        await update.message.reply_text("Нет активной игры.")
        return

    if update.effective_user.id != game["host"]:
        await update.message.reply_text(
            "Отменить игру может только создатель."
        )
        return

    game["phase"] = "finished"

    await update.message.reply_text("🛑 Игра отменена!")


# ==========================================
# HEALTH CHECK ДЛЯ RENDER
# ==========================================

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


async def post_shutdown(application):
    server = application.bot_data.get("health_server")

    if server:
        server.close()
        await server.wait_closed()


# ==========================================
# ЗАПУСК
# ==========================================

def main():
    if not TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN!")

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
    app.add_handler(CallbackQueryHandler(night_action, pattern=r"^night\|"))
    app.add_handler(CallbackQueryHandler(ready_vote, pattern=r"^ready\|"))
    app.add_handler(CallbackQueryHandler(vote, pattern=r"^vote\|"))

    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            mafia_chat
        )
    )

    logging.info("Mafia bot is starting...")
    app.run_polling()


if __name__ == "__main__":
    import asyncio
    main()
