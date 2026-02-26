import discord
from discord.ext import commands
import requests
import sqlite3
import asyncio

# --- CONFIGURATION ---
DISCORD_TOKEN = ''
TMDB_API_KEY = ''
TRAKT_CLIENT_ID = ''
DATABASE_FILE = 'movie_night.db'

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    # Table for movies
    c.execute('''CREATE TABLE IF NOT EXISTS movies 
                 (tmdb_id TEXT PRIMARY KEY, title TEXT, poster TEXT, year TEXT)''')
    # Table for votes
    c.execute('''CREATE TABLE IF NOT EXISTS votes 
                 (tmdb_id TEXT, user_id INTEGER, score INTEGER, 
                 PRIMARY KEY (tmdb_id, user_id))''')
    # Table for Trakt User Mapping
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (discord_id INTEGER PRIMARY KEY, trakt_username TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- HELPER FUNCTIONS ---

def search_tmdb(query):
    url = f"https://api.themoviedb.org/3/search/movie?query={query}&language=en-US"
    headers = {"accept": "application/json", "Authorization": f"Bearer {TMDB_API_KEY}"}
    response = requests.get(url, headers=headers).json()
    if 'results' in response and response['results']:
        return response['results'][0]
    return None

def get_tmdb_trailer(tmdb_id):
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/videos?language=en-US"
    headers = {"accept": "application/json", "Authorization": f"Bearer {TMDB_API_KEY}"}
    try:
        response = requests.get(url, headers=headers).json()
        results = response.get('results', [])
        if results:
            for video in results:
                if video['site'] == 'YouTube' and video['type'] == 'Trailer':
                    return f"https://www.youtube.com/watch?v={video['key']}"
            return f"https://www.youtube.com/watch?v={results[0]['key']}"
    except: pass
    return None

def check_trakt_history(tmdb_id):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT trakt_username FROM users")
    users = c.fetchall()
    conn.close()

    already_watched = []
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": TRAKT_CLIENT_ID
    }

    for (username,) in users:
        found_for_user = False
        page = 1
        limit = 50 # Wir laden 50 pro Seite für Speed
        
        # Wir loopen durch die Seiten (Sicherheitshalber max 100 Seiten = 5000 Filme)
        while page <= 100: 
            url = f"https://api.trakt.tv/users/{username}/history/movies?page={page}&limit={limit}"
            try:
                response = requests.get(url, headers=headers)
                if response.status_code != 200:
                    break
                
                history = response.json()
                if not history: # Keine weiteren Filme mehr in der Liste
                    break
                
                for entry in history:
                    trakt_ids = entry.get('movie', {}).get('ids', {})
                    trakt_tmdb = str(trakt_ids.get('tmdb', ''))
                    
                    if trakt_tmdb == str(tmdb_id):
                        date = entry['watched_at'][:10]
                        already_watched.append(f"**{username}** (seen {date})")
                        found_for_user = True
                        break # Film gefunden für diesen User
                
                if found_for_user:
                    break
                page += 1 # Nächste Seite laden
                
            except Exception as e:
                print(f"Trakt error for {username}: {e}")
                break
                
    return already_watched

async def get_ranking_embed():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute('''SELECT m.title, SUM(v.score) as total, MIN(v.score) as min_s, COUNT(v.user_id) 
                 FROM movies m JOIN votes v ON m.tmdb_id = v.tmdb_id 
                 GROUP BY m.tmdb_id''')
    results = c.fetchall()
    conn.close()

    if not results:
        return discord.Embed(description="No movies voted on yet!", color=discord.Color.orange())

    valid = sorted([r for r in results if r[2] > -50], key=lambda x: x[1], reverse=True)
    vetos = [r for r in results if r[2] <= -50]

    embed = discord.Embed(title="📊 Current Standings", color=discord.Color.gold())
    ranking_text = "\n".join([f"{i+1}. **{r[0]}** — `{r[1]} pts` ({r[3]} votes)" for i, r in enumerate(valid)])
    embed.description = ranking_text if ranking_text else "No movies in ranking."
    if vetos:
        embed.add_field(name="🚫 Vetoed", value=", ".join([f"~~{v[0]}~~" for v in vetos]), inline=False)
    return embed

# --- UI COMPONENTS ---

class MovieVoteView(discord.ui.View):
    def __init__(self, tmdb_id, title, trailer_url=None):
        super().__init__(timeout=None)
        self.tmdb_id = tmdb_id
        self.title = title
        if trailer_url:
            self.add_item(discord.ui.Button(label="Trailer 🍿", url=trailer_url, style=discord.ButtonStyle.link, row=2))

    async def cast_vote(self, interaction, score):
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO votes (tmdb_id, user_id, score) VALUES (?, ?, ?)",
                  (self.tmdb_id, interaction.user.id, score))
        conn.commit()
        conn.close()
        
        status = f"{score} Stars ⭐" if score >= 0 else "VETO! 🚫"
        await interaction.response.send_message(f"Your vote ({status}) for **{self.title}** counted!", ephemeral=True)
        
        ranking_embed = await get_ranking_embed()
        await interaction.channel.send(embed=ranking_embed, delete_after=20)

    @discord.ui.button(label="5 ⭐", style=discord.ButtonStyle.green, row=0)
    async def five(self, i, b): await self.cast_vote(i, 5)
    @discord.ui.button(label="4 ⭐", style=discord.ButtonStyle.green, row=0)
    async def four(self, i, b): await self.cast_vote(i, 4)
    @discord.ui.button(label="3 ⭐", style=discord.ButtonStyle.gray, row=0)
    async def three(self, i, b): await self.cast_vote(i, 3)
    @discord.ui.button(label="2 ⭐", style=discord.ButtonStyle.gray, row=1)
    async def two(self, i, b): await self.cast_vote(i, 2)
    @discord.ui.button(label="1 ⭐", style=discord.ButtonStyle.gray, row=1)
    async def one(self, i, b): await self.cast_vote(i, 1)
    @discord.ui.button(label="0 ⭐", style=discord.ButtonStyle.gray, row=1)
    async def zero(self, i, b): await self.cast_vote(i, 0)
    @discord.ui.button(label="VETO", style=discord.ButtonStyle.red, emoji="🚫", row=1)
    async def veto(self, i, b): await self.cast_vote(i, -100)

# --- COMMANDS ---

bot = commands.Bot(command_prefix='!', intents=discord.Intents.all())

@bot.command()
async def movie(ctx, *, query: str):
    """Search movie & start voting. Checks Trakt history of registered users."""
    data = search_tmdb(query)
    if not data: return await ctx.send("Movie not found.")

    tmdb_id = str(data['id'])
    title = data['title']
    year = data.get('release_date', '????')[:4]
    poster = f"https://image.tmdb.org/t/p/w500{data['poster_path']}" if data['poster_path'] else None

    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO movies VALUES (?, ?, ?, ?)", (tmdb_id, title, poster, year))
    conn.commit()
    conn.close()

    trailer_url = get_tmdb_trailer(tmdb_id)
    watched_by = check_trakt_history(tmdb_id)

    embed = discord.Embed(title=f"{title} ({year})", description=data['overview'][:400]+"...", color=0x2b2d31)
    if poster: embed.set_image(url=poster)
    embed.add_field(name="Rating", value=f"⭐ {data['vote_average']}/10", inline=True)
    
    if watched_by:
        embed.add_field(name="⚠️ Recently watched by:", value="\n".join(watched_by), inline=False)
    
    await ctx.send(embed=embed, view=MovieVoteView(tmdb_id, title, trailer_url))

@bot.command()
async def ranking(ctx):
    """Shows current standings"""
    await ctx.send(embed=await get_ranking_embed())

@bot.command()
async def set_trakt(ctx, username: str):
    """Link your Discord ID to your Public Trakt.tv username"""
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (discord_id, trakt_username) VALUES (?, ?)", (ctx.author.id, username))
    conn.commit()
    conn.close()
    await ctx.send(f"✅ Linked to Trakt user: **{username}**. Make sure your profile is public!")

@bot.command()
@commands.has_permissions(administrator=True)
async def clear(ctx):
    """Wipes movies and votes for a new session"""
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM votes"); c.execute("DELETE FROM movies")
    conn.commit(); conn.close()
    await ctx.send("🧹 Database cleared!")

@bot.event
async def on_ready(): print(f'Logged in as {bot.user.name}')

bot.run(DISCORD_TOKEN)