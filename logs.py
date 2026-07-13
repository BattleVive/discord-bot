import os,logging
os.makedirs("logs", exist_ok=True)

formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

bot_handler = logging.FileHandler(filename="logs/bot.log", encoding="utf-8", mode="a")
bot_handler.setFormatter(formatter)

discord_handler = logging.FileHandler(filename="logs/discord.log", encoding="utf-8", mode="a")
discord_handler.setFormatter(formatter)

logger = logging.getLogger("bot")  # your own namespace for calls like logger.info(...)
logger.setLevel(logging.INFO)
logger.addHandler(bot_handler)

discord_logger = logging.getLogger("discord")  # library"s namespace
discord_logger.setLevel(logging.ERROR)
discord_logger.addHandler(discord_handler)