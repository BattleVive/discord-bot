#!/usr/bin/env python3
# pyrefly: ignore [missing-import]
import discord
# pyrefly: ignore [missing-import]
from discord.ext import tasks,commands
import logging
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
import os
import requests

load_dotenv()
SUPABASE_API_KEY = os.getenv('SUPABASE_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')

class BattlevivieTokenManager:
    def __init__(self, JWT_token, refresh_token):
        self.JWT_token = JWT_token 
        self.refresh_token = refresh_token
    
    def revalidate(refresh_token):
        print("Revalidating tokens!")
        headers = {
            "Content-Type": "application/json",
            "apikey": SUPABASE_API_KEY
        }
        json = {
            "refresh_token": refresh_token 
        }
        response = requests.post(f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token", headers=headers, json=json)
        data=response.json()
        new_refresh_token =data["refresh_token"] 
        new_JWT= data["access_token"] 

        return new_refresh_token,new_JWT  



handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')

battlevive_tokens= BattlevivieTokenManager(JWT_token=os.getenv('BOOTSTRAP_JWT'),refresh_token=os.getenv('BOOTSTRAP_REFRESH_TOKEN'))

discord_bot_token = os.getenv('DISCORD_TOKEN')


bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

@tasks.loop(minutes=30)
async def revalidate_tokens():
    new_refresh_token, new_JWT = BattlevivieTokenManager.revalidate(refresh_token=battlevive_tokens.refresh_token)
    battlevive_tokens.JWT_token= new_JWT
    battlevive_tokens.refresh_token = new_refresh_token

@bot.event
async def setup_hook():
    revalidate_tokens.start()


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot.run(discord_bot_token)
