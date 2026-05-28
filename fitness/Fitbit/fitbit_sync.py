#!/usr/bin/env python3
import os
import json
import requests
import subprocess
import sys
from datetime import datetime
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

CLIENT_ID = os.getenv("FITBIT_CLIENT_ID")
CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ Missing FITBIT_CLIENT_ID or FITBIT_CLIENT_SECRET in .env file")
    sys.exit(1)

TOKEN_FILE = "fitness/Fitbit/.tokens.json"
DATA_DIR = "fitness/data"
os.makedirs(DATA_DIR, exist_ok=True)

SCOPES = "activity heartrate location nutrition profile settings sleep social weight"
REDIRECT_URI = "http://localhost:8080/"

def load_tokens():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return None

def save_tokens(tokens):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f)

def get_access_token():
    tokens = load_tokens()
    now = datetime.now().timestamp()

    if tokens and tokens.get("expires_at", 0) > now + 60:
        return tokens["access_token"]

    if tokens and "refresh_token" in tokens:
        print("🔄 Refreshing Fitbit access token...")
        response = requests.post(
            "https://api.fitbit.com/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            },
            auth=(CLIENT_ID, CLIENT_SECRET),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code == 200:
            new_tokens = response.json()
            new_tokens["expires_at"] = now + new_tokens["expires_in"]
            save_tokens(new_tokens)
            return new_tokens["access_token"]

    print("🔑 Opening authorization URL...")
    auth_url = f"https://www.fitbit.com/oauth2/authorize?response_type=code&client_id={CLIENT_ID}&scope={SCOPES.replace(' ', '%20')}&redirect_uri={REDIRECT_URI.replace(':', '%3A').replace('/', '%2F')}"
    print(f"\nOpen this URL:\n{auth_url}\n")
    code = input("Paste the code from the URL: ").strip()

    response = requests.post(
        "https://api.fitbit.com/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if response.status_code != 200:
        print("❌ Authorization failed:", response.text)
        sys.exit(1)

    tokens = response.json()
    tokens["expires_at"] = now + tokens["expires_in"]
    save_tokens(tokens)
    print("✅ Tokens saved!")
    return tokens["access_token"]

def pull_fitbit_data(access_token):
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"📥 Pulling data for {today}...")

    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(f"https://api.fitbit.com/1/user/-/activities/date/{today}.json", headers=headers)

    data = r.json().get("summary", {}) if r.status_code == 200 else {}

    csv_path = os.path.join(DATA_DIR, f"daily_{today}.csv")
    df = pd.DataFrame([{
        "date": today,
        "steps": data.get("steps", 0),
        "calories_out": data.get("caloriesOut", 0),
        "active_minutes": data.get("veryActiveMinutes", 0) + data.get("fairlyActiveMinutes", 0),
        "lightly_active_minutes": data.get("lightlyActiveMinutes", 0),
        "sedentary_minutes": data.get("sedentaryMinutes", 0),
        "distance": data.get("distances", [{}])[0].get("distance", 0) if data.get("distances") else 0,
    }])
    df.to_csv(csv_path, index=False)
    print(f"✅ Saved to {csv_path}")
    return csv_path

def git_commit_and_push(file_path):
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        subprocess.check_call(["git", "add", file_path])
        subprocess.check_call(["git", "commit", "-m", f"chore: update Fitbit data {today}"])
        subprocess.check_call(["git", "push", "origin", "master"])
        print("✅ Pushed to GitHub!")
    except subprocess.CalledProcessError as e:
        if "nothing to commit" in str(e):
            print("✅ No changes.")
        else:
            print("⚠️ Git error:", e)

if __name__ == "__main__":
    access_token = get_access_token()
    csv_file = pull_fitbit_data(access_token)
    git_commit_and_push(csv_file)