import discord
from discord.ext import commands
import requests
import sqlite3

# --- CONFIGURATION ---
DISCORD_TOKEN = ''
TMDB_API_KEY = ''
DATABASE_FILE = 'movie_voting.db'

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS movies 
                 (tmdb_id TEXT PRIMARY KEY, title TEXT, poster TEXT, year TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS votes 
                 (tmdb_id TEXT, user_id INTEGER, score INTEGER, 
                 PRIMARY KEY (tmdb_id, user_id))''')
    conn.commit()
    conn.close()

init_db()

# --- TMDB HELPER FUNCTIONS ---
def search_tmdb(query):
    # Language set to en-US for English results
    url = f"https://api.themoviedb.org/3/search/movie?query={query}&language=en-US"
    headers = {"accept": "application/json", "Authorization": f"Bearer {TMDB_API_KEY}"}
    response = requests.get(url, headers=headers).json()
    if 'results' in response and response['results']:
        return response['results'][0]
    return None

def get_tmdb_trailer(tmdb_id):
    # Searches for English trailers
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/videos?language=en-US"
    headers = {"accept": "application/json", "Authorization": f"Bearer {TMDB_API_KEY}"}
    response = requests.get(url, headers=headers).json()
    
    if response.get('results'):
        for video in response['results']:
            if video['site'] == 'YouTube' and video['type'] == 'Trailer':
                return f"https://www.youtube.com/watch?v={video['key']}"
    return None

async def get_ranking_embed():
    """Generates the current ranking embed"""
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute('''SELECT m.title, SUM(v.score) as total, MIN(v.score) as min_s, COUNT(v.user_id) 
                 FROM movies m JOIN votes v ON m.tmdb_id = v.tmdb_id 
                 GROUP BY m.tmdb_id''')
    results = c.fetchall()
    conn.close()

    if not results:
        return discord.Embed(description="No votes yet!", color=discord.Color.orange())

    # Filter out movies with a Veto (score <= -50)
    valid = sorted([r for r in results if r[2] > -50], key=lambda x: x[1], reverse=True)
    vetos = [r for r in results if r[2] <= -50]

    embed = discord.Embed(title="📊 Current Standings", color=discord.Color.gold())
    
    ranking_text = ""
    for i, (title, score, _, count) in enumerate(valid, 1):
        ranking_text += f"{i}. **{title}** — `{score} pts` ({count} votes)\n"
    
    embed.description = ranking_text if ranking_text else "No valid movies in ranking."
    if vetos:
        embed.add_field(name="🚫 Vetoed", value=", ".join([f"~~{v[0]}~~" for v in vetos]), inline=False)
    
    return embed

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# --- UI BUTTONS ---
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
        
        rating_msg = f"{score} Stars ⭐" if score >= 0 else "VETO! 🚫"
        
        # 1. Private confirmation for the user
        await interaction.response.send_message(f"Your vote ({rating_msg}) for **{self.title}** has been counted!", ephemeral=True)
        
        # 2. Automatic ranking update for the channel
        ranking_embed = await get_ranking_embed()
        await interaction.channel.send(embed=ranking_embed, delete_after=30) # Self-destructs after 30s to keep chat clean

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

@bot.command()
async def movie(ctx, *, query: str):
    """Search for a movie and start voting"""
    movie_data = search_tmdb(query)
    if not movie_data:
        await ctx.send("Nothing found! Please check the movie title.")
        return

    tmdb_id = str(movie_data['id'])
    title = movie_data['title']
    year = movie_data.get('release_date', '????')[:4]
    poster = f"https://image.tmdb.org/t/p/w500{movie_data['poster_path']}" if movie_data['poster_path'] else None
    
    # Save to database
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO movies VALUES (?, ?, ?, ?)", (tmdb_id, title, poster, year))
    conn.commit()
    conn.close()

    trailer_url = get_tmdb_trailer(tmdb_id)
    
    embed = discord.Embed(title=f"{title} ({year})", description=movie_data['overview'][:400]+"...", color=0x2f3136)
    if poster: embed.set_image(url=poster)
    embed.add_field(name="TMDB Rating", value=f"⭐ {movie_data['vote_average']}/10", inline=True)
    embed.set_footer(text="Vote below or suggest another movie with !movie")
    
    await ctx.send(embed=embed, view=MovieVoteView(tmdb_id, title, trailer_url))

@bot.command()
async def ranking(ctx):
    """Show the current movie ranking"""
    embed = await get_ranking_embed()
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def clear(ctx):
    """Clears the database (Admins only)"""
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM votes")
    c.execute("DELETE FROM movies")
    conn.commit()
    conn.close()
    await ctx.send("🧹 Database cleared! Ready for a new movie night.")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')

bot.run(DISCORD_TOKEN)