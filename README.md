# WIP discord bot for battlevive website 

# preperation
requires browser and manual login, do this on client and manualy copy .env for prod
```bash
cd utils
```
fill up .env.example
```bash
mv .env.example .env
chmod +x init.sh
./init.sh
cd ..
```
# run
```bash
docker compose build
docker compose up -d
```
or 
```bash
podman compose build
podman compose up -d
```


