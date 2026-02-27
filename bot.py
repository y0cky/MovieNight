import discord
from discord.ext import commands
from discord import app_commands
import requests
import sqlite3
import asyncio
import random

# --- CONFIGURATION ---
DISCORD_TOKEN = ''
TMDB_API_KEY = ''
TRAKT_CLIENT_ID = ''
DATABASE_FILE = 'movie_night.db'
JELLYSEERR_BASE_URL = None 

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS movies (tmdb_id TEXT PRIMARY KEY, title TEXT, poster TEXT, year TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS votes (tmdb_id TEXT, user_id INTEGER, score INTEGER, PRIMARY KEY (tmdb_id, user_id))')
    c.execute('CREATE TABLE IF NOT EXISTS users (discord_id INTEGER PRIMARY KEY, trakt_username TEXT)')
    conn.commit(); conn.close()

init_db()

# --- HELPER FUNCTIONS ---

def get_tmdb_suggestions(query: str):
    if not query or len(query) < 3: return []
    url = f"https://api.themoviedb.org/3/search/movie?query={query}&language=en-US&include_adult=false"
    headers = {"accept": "application/json", "Authorization": f"Bearer {TMDB_API_KEY}"}
    try:
        response = requests.get(url, headers=headers, timeout=2.0).json()
        results = response.get('results', [])[:10]
        return [app_commands.Choice(name=f"{m['title']} ({m.get('release_date', '')[:4]})", value=str(m['id'])) for m in results]
    except: return []

def search_tmdb_by_id(tmdb_id):
    headers = {"accept": "application/json", "Authorization": f"Bearer {TMDB_API_KEY}"}
    r = requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}?language=en-US", headers=headers).json()
    return r if 'id' in r else None

def get_tmdb_trailer(tmdb_id):
    headers = {"accept": "application/json", "Authorization": f"Bearer {TMDB_API_KEY}"}
    try:
        res = requests.get(f"https://api.themoviedb.org/3/movie/{tmdb_id}/videos?language=en-US", headers=headers).json()
        for video in res.get('results', []):
            if video['site'] == 'YouTube' and video['type'] == 'Trailer':
                return f"https://www.youtube.com/watch?v={video['key']}"
        return None
    except: return None

def check_trakt_data(tmdb_id):
    conn = sqlite3.connect(DATABASE_FILE); c = conn.cursor()
    c.execute("SELECT trakt_username FROM users"); users = c.fetchall(); conn.close()
    reports = []
    headers = {"Content-Type": "application/json", "trakt-api-version": "2", "trakt-api-key": TRAKT_CLIENT_ID}
    for (username,) in users:
        try:
            # Collection Check
            r_coll = requests.get(f"https://api.trakt.tv/users/{username}/collection/movies", headers=headers, timeout=10)
            if r_coll.status_code == 200:
                if any(str(e.get('movie', {}).get('ids', {}).get('tmdb', '')) == str(tmdb_id) for e in r_coll.json()):
                    reports.append(f"📦 **{username}** has this in their **Collection**")
            
            # History Check (High limit for deep search)
            r_hist = requests.get(f"https://api.trakt.tv/users/{username}/history/movies?limit=5000", headers=headers, timeout=15)
            if r_hist.status_code == 200:
                for entry in r_hist.json():
                    if str(entry.get('movie', {}).get('ids', {}).get('tmdb', '')) == str(tmdb_id):
                        reports.append(f"👁️ **{username}** watched this on {entry['watched_at'][:10]}")
                        break
        except: pass
    return reports

async def get_ranking_embed(user_ids=None):
    conn = sqlite3.connect(DATABASE_FILE); c = conn.cursor()
    if user_ids:
        placeholders = ', '.join(['?'] * len(user_ids))
        c.execute(f'SELECT m.title, SUM(v.score), MIN(v.score), COUNT(v.user_id) FROM movies m JOIN votes v ON m.tmdb_id = v.tmdb_id WHERE v.user_id IN ({placeholders}) GROUP BY m.tmdb_id', user_ids)
    else:
        c.execute('SELECT m.title, SUM(v.score), MIN(v.score), COUNT(v.user_id) FROM movies m JOIN votes v ON m.tmdb_id = v.tmdb_id GROUP BY m.tmdb_id')
    results = c.fetchall(); conn.close()
    if not results: return discord.Embed(description="No active movies in the leaderboard!", color=discord.Color.orange())
    valid = sorted([r for r in results if r[2] > -50], key=lambda x: x[1], reverse=True)
    vetos = [r for r in results if r[2] <= -50]
    embed = discord.Embed(title="📊 Movie Ranking Standings", color=discord.Color.gold())
    embed.description = "\n".join([f"{i+1}. **{r[0]}** — `{r[1]} pts` ({r[3]} votes)" for i, r in enumerate(valid)]) or "None"
    if vetos: embed.add_field(name="🚫 Vetoed", value=", ".join([f"~~{v[0]}~~" for v in vetos]), inline=False)
    return embed

# --- UI COMPONENTS ---

class MovieVoteView(discord.ui.View):
    def __init__(self, tmdb_id, title, imdb_id=None, trailer_url=None):
        super().__init__(timeout=None)
        self.tmdb_id, self.title = tmdb_id, title
        
        if trailer_url: self.add_item(discord.ui.Button(label="Trailer 🍿", url=trailer_url, row=2))
        self.add_item(discord.ui.Button(label="TMDB 🎬", url=f"https://www.themoviedb.org/movie/{tmdb_id}", row=2))
        if imdb_id: self.add_item(discord.ui.Button(label="IMDb 🎥", url=f"https://www.imdb.com/title/{imdb_id}", row=2))
        self.add_item(discord.ui.Button(label="Letterboxd 💚", url=f"https://letterboxd.com/tmdb/{tmdb_id}", row=3))
        search_title = title.replace(" ", "+")
        self.add_item(discord.ui.Button(label="JustWatch 📺", url=f"https://www.justwatch.com/de/Suche?q={search_title}", row=3))
        if JELLYSEERR_BASE_URL: self.add_item(discord.ui.Button(label="Jellyseerr 📥", url=f"{JELLYSEERR_BASE_URL.rstrip('/')}/movie/{tmdb_id}", row=3))

    async def cast_vote(self, interaction, score):
        conn = sqlite3.connect(DATABASE_FILE); c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO votes VALUES (?, ?, ?)", (self.tmdb_id, interaction.user.id, score))
        conn.commit(); conn.close()
        icons = {5: "🔥", 4: "✅", 3: "🆗", 2: "🤨", 1: "🥱", 0: "💩", -100: "⛔"}
        icon = icons.get(score, "⭐")
        await interaction.response.send_message(f"{icon} {interaction.user.mention} voted {'VETO' if score < 0 else f'{score} Stars'} for **{self.title}**!", allowed_mentions=discord.AllowedMentions.none())
        await interaction.channel.send(embed=await get_ranking_embed(), delete_after=15)

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

class VoteDropdown(discord.ui.Select):
    def __init__(self, movies):
        options = [discord.SelectOption(label=f"{m[1]} ({m[2]})", value=m[0]) for m in movies]
        super().__init__(placeholder="Select a movie to open the voting card...", options=options)

    async def callback(self, interaction: discord.Interaction):
        # STABILITY FIX: Tell Discord we are working
        await interaction.response.defer()
        
        tmdb_id = self.values[0]
        data = search_tmdb_by_id(tmdb_id)
        if not data: return await interaction.followup.send("Failed to fetch movie data.", ephemeral=True)
        
        title, year = data['title'], data.get('release_date', '????')[:4]
        poster = f"https://image.tmdb.org/t/p/w500{data.get('poster_path', '')}" if data.get('poster_path') else None
        rating = data.get('vote_average', '0')
        
        tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}"
        embed = discord.Embed(title=f"{title} ({year})", url=tmdb_url, description=data.get('overview', '')[:450]+"...", color=0x2b2d31)
        if poster: embed.set_image(url=poster)
        embed.add_field(name="TMDB Rating", value=f"⭐ **{rating}/10**", inline=True)
        
        # This can take 5-10 seconds with high limits
        trakt_info = check_trakt_data(tmdb_id)
        if trakt_info: embed.add_field(name="🛰️ Trakt Intelligence", value="\n".join(trakt_info), inline=False)
        
        # Use followup because of defer()
        await interaction.followup.send(embed=embed, view=MovieVoteView(tmdb_id, title, data.get('imdb_id'), get_tmdb_trailer(tmdb_id)))

class VoteView(discord.ui.View):
    def __init__(self, movies):
        super().__init__()
        self.add_item(VoteDropdown(movies))

# --- BOT CLASS ---
class MovieBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())
    async def setup_hook(self):
        await self.tree.sync()
        print(f"✅ Synced Slash Commands for {self.user}")

bot = MovieBot()

# --- SLASH COMMANDS ---

@bot.tree.command(name="movie", description="Search and add a movie to the list")
async def movie(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    data = search_tmdb_by_id(query) if query.isdigit() else None
    if not data: return await interaction.followup.send("Movie not found. Use the dropdown!")
    
    tmdb_id, title, year = str(data['id']), data['title'], data.get('release_date', '????')[:4]
    poster = f"https://image.tmdb.org/t/p/w500{data.get('poster_path', '')}" if data.get('poster_path') else None
    rating = data.get('vote_average', '0')
    
    conn = sqlite3.connect(DATABASE_FILE); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO movies VALUES (?, ?, ?, ?)", (tmdb_id, title, poster, year))
    conn.commit(); conn.close()

    tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}"
    embed = discord.Embed(title=f"{title} ({year})", url=tmdb_url, description=data.get('overview', '')[:450]+"...", color=0x2b2d31)
    if poster: embed.set_image(url=poster)
    embed.add_field(name="TMDB Rating", value=f"⭐ **{rating}/10**", inline=True)
    
    trakt_info = check_trakt_data(tmdb_id)
    if trakt_info: embed.add_field(name="🛰️ Trakt Intelligence", value="\n".join(trakt_info), inline=False)
    
    await interaction.followup.send(embed=embed, view=MovieVoteView(tmdb_id, title, data.get('imdb_id'), get_tmdb_trailer(tmdb_id)))

@movie.autocomplete('query')
async def movie_autocomplete(interaction, current: str):
    return get_tmdb_suggestions(current)

@bot.tree.command(name="vote", description="Vote for a movie from the candidate list")
async def vote_list(interaction: discord.Interaction):
    conn = sqlite3.connect(DATABASE_FILE); c = conn.cursor()
    c.execute("SELECT tmdb_id, title, year FROM movies")
    movies = c.fetchall(); conn.close()
    if not movies: return await interaction.response.send_message("No movies found! Use `/movie` first.", ephemeral=True)
    await interaction.response.send_message("Select a movie:", view=VoteView(movies), ephemeral=True)

@bot.tree.command(name="ranking", description="Show movie ranking")
async def ranking(interaction: discord.Interaction, user1: discord.Member = None, user2: discord.Member = None):
    selected = [u.id for u in [user1, user2] if u]
    await interaction.response.send_message(embed=await get_ranking_embed(selected if selected else None))

@bot.tree.command(name="watched", description="Remove a movie from the candidate list")
async def watched(interaction: discord.Interaction, movie_title: str):
    conn = sqlite3.connect(DATABASE_FILE); c = conn.cursor()
    c.execute("SELECT tmdb_id FROM movies WHERE title = ?", (movie_title,))
    res = c.fetchone()
    if res:
        c.execute("DELETE FROM votes WHERE tmdb_id = ?", (res[0],))
        c.execute("DELETE FROM movies WHERE tmdb_id = ?", (res[0],))
        conn.commit(); conn.close()
        await interaction.response.send_message(f"✅ **{movie_title}** removed.")
    else:
        conn.close(); await interaction.response.send_message("Movie not found.", ephemeral=True)

@bot.tree.command(name="set_trakt", description="Link your Trakt account")
async def set_trakt(interaction: discord.Interaction, username: str):
    conn = sqlite3.connect(DATABASE_FILE); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (discord_id, trakt_username) VALUES (?, ?)", (interaction.user.id, username))
    conn.commit(); conn.close()
    await interaction.response.send_message(f"✅ Linked to: **{username}**", ephemeral=True)

@bot.tree.command(name="pick", description="Pick a random winner from the Top 3")
async def pick(interaction: discord.Interaction):
    conn = sqlite3.connect(DATABASE_FILE); c = conn.cursor()
    c.execute('SELECT m.title, SUM(v.score), MIN(v.score) FROM movies m JOIN votes v ON m.tmdb_id = v.tmdb_id GROUP BY m.tmdb_id HAVING MIN(v.score) > -50 ORDER BY SUM(v.score) DESC')
    results = c.fetchall(); conn.close()
    if not results: return await interaction.response.send_message("No movies!")
    winner = random.choice(results[:3])
    await interaction.response.send_message("🥁 Rolling...")
    await asyncio.sleep(2)
    await interaction.edit_original_response(content=None, embed=discord.Embed(title="🎲 Winner", description=f"# 🏆 {winner[0]}", color=discord.Color.purple()))

@bot.tree.command(name="clear", description="Reset database (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def clear(interaction: discord.Interaction):
    conn = sqlite3.connect(DATABASE_FILE); c = conn.cursor()
    c.execute("DELETE FROM votes"); c.execute("DELETE FROM movies")
    conn.commit(); conn.close()
    await interaction.response.send_message(embed=discord.Embed(title="🧹 Reset", description="Database cleared.", color=discord.Color.red()))

@bot.command()
@commands.has_permissions(administrator=True)
async def sync(ctx):
    await bot.tree.sync()
    await ctx.send("✅ Sync complete.")

bot.run(DISCORD_TOKEN)