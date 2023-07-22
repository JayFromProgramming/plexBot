import asyncio
import datetime

import humanize
import requests
import os

import discord
import plexapi
from discord.ext.commands import command, has_permissions, Cog, BadArgument, is_owner
from loguru import logger as logging
from ConcurrentDatabase.Database import Database
import qbittorrentapi
from discord.ui import View, Button, Select


class PlexSelfService(Cog):

    def __init__(self, bot):
        self.bot = bot
        if not os.path.exists("rarbg_db/rarbg_db.sqlite"):
            # Disable this cog if the rarbg database doesn't exist
            raise Exception("rarbg database not found")
        self.rargb_database = Database("rarbg_db/rarbg_db.sqlite")
        self.bot.database.create_table("qbittorrent_servers", {"guild_id": "INTEGER", "host": "TEXT", "port": "INTEGER",
                                                               "username": "TEXT", "password": "TEXT",
                                                               "default_movie_library": "TEXT",
                                                               "default_tv_library": "TEXT"})
        with open("rarbg_db/trackers.txt", "r") as f:
            self.trackers = f.read().splitlines()
            # Remove empty lines
            self.trackers = [x for x in self.trackers if x != ""]

    def make_magnet(self, torrent_entry):
        return f"magnet:?xt=urn:btih:{torrent_entry['hash']}&dn={torrent_entry['title']}"

    def get_qbittorrent(self, guild_id):
        table = self.bot.database.get_table("qbittorrent_servers")
        qbittorrent = table.get_row(guild_id=guild_id)
        if qbittorrent is None:
            return None
        return qbittorrentapi.Client(host=qbittorrent["host"], port=qbittorrent["port"],
                                     username=qbittorrent["username"], password=qbittorrent["password"])

    async def add_torrent(self, interaction, torrent_id, guild_id):
        guild = self.bot.get_guild(guild_id)
        plex = await self.bot.fetch_plex(guild)
        torrent_entry = self.rargb_database.get_table("items").get_row(id=torrent_id)
        magnet = self.make_magnet(torrent_entry)
        # Determine save path based on category
        category = torrent_entry["cat"]
        if "movies" in category:
            # Get the default movie library
            library_name = self.bot.database.get_table("qbittorrent_servers").get_row(
                guild_id=guild_id)["default_movie_library"]
            # Get the library from the Plex server
            library = plex.library.section(library_name)
            other_libraries = [x for x in plex.library.sections() if x != library and x.type == "movie"]
            # Get the path to the library
        elif "tv" in category:
            # Get the default movie library
            library_name = self.bot.database.get_table("qbittorrent_servers").get_row(
                guild_id=guild_id)["default_tv_library"]
            # Get the library from the Plex server
            library = plex.library.section(library_name)
            other_libraries = [x for x in plex.library.sections() if x != library and x.type == "show"]
        else:
            raise Exception("Unknown category")
        # Create confirmation message
        embed = discord.Embed(title="Add Torrent", description=f"Add `{torrent_entry['title']}` to `{library_name}`?",
                              color=discord.Color.green())
        # Add a select menu to the message for selecting the target library
        select = Select(placeholder=library.title, min_values=1, max_values=1)
        # Add the libraries to the select menu
        for library in other_libraries:
            select.add_option(label=library.title, value=library.title)
        # Add the select menu to the message
        view = View()
        view.add_item(select)
        confirm = Button(label="Confirm", style=discord.ButtonStyle.green, custom_id=torrent_id)
        view.add_item(confirm)
        cancel = Button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancel")
        view.add_item(cancel)
        confirm.callback = self.confirmation_callback
        select.callback = self.confirmation_callback
        # Add the torrent_id as a custom_id to the view
        # Send the message
        await interaction.response.edit_message(embed=embed, view=view)

    async def callback(self, interaction):
        # Get the torrent_id from the value of the selected option
        try:
            torrent_id = interaction.data["values"][0]
            await self.add_torrent(interaction, torrent_id, interaction.guild_id)
            # await interaction.response.send_message(f"Added torrent `{torrent_id}` to qbittorrent")
        except Exception as e:
            logging.exception(e)
            await interaction.response.send_message(f"PlexBot encountered an error adding the torrent to qbittorrent:"
                                                    f"```{e}```")

    async def confirmation_callback(self, interaction):
        try:
            logging.info(interaction.data)
            guild = self.bot.get_guild(interaction.guild_id)
            plex = await self.bot.fetch_plex(guild)
            qbittorrent = self.get_qbittorrent(guild.id)
            # If the data is a select menu then the user has selected a library
            # Otherwise the user has clicked the confirm button and we should use the default library
            if interaction.data["component_type"] == 3:
                library_name = interaction.data["values"][0]
            else:  # Get the default value of the select menu from the interaction
                library_name = interaction.message.components[0].children[0].placeholder
            torrent_id = interaction.data["custom_id"]
            # Get the library from the Plex server
            library = plex.library.section(library_name)
            torrent_entry = self.rargb_database.get_table("items").get_row(id=torrent_id)
            magnet = self.make_magnet(torrent_entry)
            # Get the path to the library
            path = library.locations[0]
            logging.info(f"Adding torrent `{torrent_entry['title']}` to `{library_name}` path `{path}`")
            # Add the torrent to qbittorrent
            result = qbittorrent.torrents_add(urls=magnet, save_path=path, content_layout="Subfolder")
            # Add trackers to the torrent
            qbittorrent.torrents_add_trackers(hash=torrent_entry["hash"], urls=self.trackers)
            if result == "Ok.":
                embed = discord.Embed(title="Torrent Added",
                                      description=f"Added `{torrent_entry['title']}` to `{library_name}`",
                                        color=discord.Color.green())
                embed.set_footer(
                    text="When this media finishes downloading a message will be sent in the new content channel")
            elif result == "Fails.":
                embed = discord.Embed(title="Torrent Add Failed",
                                      description=f"Failed to add `{torrent_entry['title']}` to `{library_name}`",
                                        color=discord.Color.red())
            else:
                embed = discord.Embed(title="Unexpected qbittorrent Response",
                                        description=f"Unexpected response from qbittorrent: `{result}`",
                                            color=discord.Color.yellow())
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception as e:
            logging.exception(e)
            await interaction.response.send_message(f"PlexBot encountered an error adding the torrent to qbittorrent:"
                                                    f"```{e}```")

    @command(name="add_content", aliases=["css"], brief="Add content to Plex",
             description="Search the internal RARBG database for content and then add it to Plex")
    async def rarbg_search(self, ctx, *, search_string):
        async with ctx.typing():
            logging.info(f"Searching Rarbg for {search_string}")
            if search_string.startswith("tt"):
                # Search by IMDB ID
                cursor = self.rargb_database.execute("SELECT * FROM main.items WHERE imdb = ?", (search_string,))
            else:
                # split the string into a list of words for better searching
                search_words = search_string.split(" ")
                cursor = self.rargb_database.execute("SELECT * FROM main.items WHERE title LIKE ? AND cat != 'xxx'",
                                                     ("%{}%".format("%".join(search_words)),))
            results = cursor.fetchall()
            if len(results) == 0:
                await ctx.send("No torrents found")
                return
            # Now filter out any results that are not rarbg x265 1080p
            valid_encodings = ["x265", "H.265", "H265"]
            valid_resolutions = ["1080p", "2160p"]
            valid_releasers = ["RARBG", "YTS"]
            filtered_results = []
            for result in results:
                # Make sure the title contains a valid encoding
                if not any(encoding in result[2] for encoding in valid_encodings):
                    continue
                # Make sure the title contains a valid resolution
                if not any(resolution in result[2] for resolution in valid_resolutions):
                    continue
                # Make sure the title contains a valid releaser
                if not any(releaser in result[2] for releaser in valid_releasers):
                    continue
                filtered_results.append(result)
            if len(filtered_results) == 0:
                await ctx.send("No valid torrents found")
                return
            # Check if those torrents are already in qbittorrent
            qbittorrent = self.get_qbittorrent(ctx.guild.id)
            # Look up the torrents in qbittorrent
            # Print all the results that are already in qbittorrent to log
            already_added = []
            for result in filtered_results:
                try:
                    qbittorrent.torrents_properties(hash=result[1])
                    # Remove the torrent from the list of results
                    already_added.append(result)
                except qbittorrentapi.exceptions.NotFound404Error:
                    pass
            # Sort the results by if they have been added or not and then alphabetically
            filtered_results.sort(key=lambda x: x[2])
            embed = discord.Embed(title="Rarbg Search Results",
                                  description=f"Searched for `{search_string}`, "
                                              f"found `{len(filtered_results)}` results",
                                  color=discord.Color.blue())
            for result in filtered_results if len(filtered_results) < 10 else filtered_results[:10]:
                # Check if the torrent is already in qbittorrent if so then add a checkmark to the title
                embed.add_field(name=f"{result[2]} {'✅' if result in already_added else ''}",
                                value=f"Size: `{humanize.naturalsize(result[5])}`,"
                                      f" Category: `{result[4]}`, IMDB: `{result[7]}`",
                                inline=False)
            if len(filtered_results) > 10:
                embed.set_footer(text="Only showing first 10 results, try a more specific search")
            # Add a selection menu to the embed to allow the user to select a torrent
            view = View(timeout=15)
            select = Select(placeholder="Select a torrent", min_values=1, max_values=1)
            for result in filtered_results if len(filtered_results) < 10 else filtered_results[:10]:
                select.add_option(label=result[2][:100], value=result[0], description=f"Hash: {result[1]}")
            view.add_item(select)
            # Attach the callback to the selection menu
            select.callback = self.callback

            await ctx.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(PlexSelfService(bot))
