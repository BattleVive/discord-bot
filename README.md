# WIP discord bot for battlevive website 

### install deps:
```bash
pip install -r "requirements.txt"
# playwrite required for generating .env for prod generate .env on your client and skip this step

playwright install chromium
```
## generate .env
```bash
cd utils
cp .env.example .env
# go to utils copy example .env and fill api key and discord bot token
python3 env_gen.py
```

After running env_gen.py login into scraping discord account and press enter after you done, keys generated should be valid for an hour after that you need to regenerate if bot is not running yet. Discord cookie should be saved making login faster.

