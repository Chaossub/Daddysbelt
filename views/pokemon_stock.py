from __future__ import annotations

import discord

from services.retailer_stock import fetch_product_status, normalize_product_url
from views.common import AuthorizedView

MAX_PRODUCTS = 30


def stock_embed(guild: discord.Guild, profile: dict) -> discord.Embed:
    config = profile.get("pokemon_stock", {})
    enabled = bool(config.get("enabled", False))
    channel_id = config.get("channel_id")
    role_id = config.get("ping_role_id")
    alerts = config.get("alerts", {})
    products = config.get("products", [])
    embed = discord.Embed(
        title="🛒 Pokémon Stock Checker",
        description=(
            "Watch Best Buy, Walmart, Target, and Barnes & Noble product links. "
            "The bot checks every ten minutes and can alert for restocks, sellouts, and price changes."
        ),
    )
    embed.add_field(name="Monitoring", value="🟢 Enabled" if enabled else "🔴 Disabled")
    embed.add_field(name="Alert channel", value=f"<#{channel_id}>" if channel_id else "Not selected")
    embed.add_field(name="Ping role", value=f"<@&{role_id}>" if role_id else "No role ping")
    embed.add_field(
        name="Alert types",
        value=(
            f"{'✅' if alerts.get('in_stock', True) else '❌'} Restocks\n"
            f"{'✅' if alerts.get('out_of_stock', False) else '❌'} Sold out\n"
            f"{'✅' if alerts.get('price_change', False) else '❌'} Price changes"
        ),
    )
    embed.add_field(name="Watched products", value=f"{len(products)}/{MAX_PRODUCTS}")
    if products:
        lines = []
        for item in products[:10]:
            state = item.get("available")
            icon = "✅" if state is True else "❌" if state is False else "❔"
            name = item.get("name") or item.get("sku") or "Product"
            store = item.get("store") or "Store"
            lines.append(f"{icon} **{store}:** [{discord.utils.escape_markdown(str(name))}]({item['url']})")
        embed.add_field(name="Products", value="\n".join(lines), inline=False)
    embed.set_footer(text="Unknown or blocked checks never trigger an alert.")
    return embed


class AddProductModal(discord.ui.Modal, title="Add Pokémon product"):
    product = discord.ui.TextInput(
        label="Product link",
        placeholder="Paste a Best Buy, Walmart, Target, or Barnes & Noble link",
        max_length=500,
    )

    def __init__(self, view: "PokemonStockView") -> None:
        super().__init__()
        self.parent_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        profile = await self.parent_view.bot.database.get_guild_profile(interaction.guild.id) or {}
        if len(profile.get("pokemon_stock", {}).get("products", [])) >= MAX_PRODUCTS:
            await interaction.followup.send(f"This server already has the maximum of {MAX_PRODUCTS} watched products.", ephemeral=True)
            return
        try:
            url, _ = normalize_product_url(str(self.product))
            status = await fetch_product_status(url)
            profile = await self.parent_view.bot.database.add_stock_product(
                interaction.guild.id,
                url=status.url,
                store=status.store,
                sku=status.sku,
                name=status.name,
                price=status.price,
                image_url=status.image_url,
                available=status.available,
                detail=status.detail,
                created_by=interaction.user.id,
            )
        except Exception as exc:
            await interaction.followup.send(f"I couldn't add that product: {exc}", ephemeral=True)
            return
        if profile is None:
            await interaction.followup.send("That product may already be watched, or it could not be saved.", ephemeral=True)
            return
        await interaction.message.edit(
            embed=stock_embed(interaction.guild, profile),
            view=PokemonStockView(
                bot=self.parent_view.bot,
                guild_id=self.parent_view.guild_id,
                requester_id=self.parent_view.requester_id,
                database_connected=self.parent_view.database_connected,
            ),
        )
        await interaction.followup.send(
            f"{status.store} product added. Its current status was saved without sending an alert.",
            ephemeral=True,
        )


class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "PokemonStockView") -> None:
        super().__init__(
            placeholder="Choose the stock-alert channel",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        selected = self.values[0]
        profile = await self.parent_view.bot.database.set_stock_channel(interaction.guild.id, selected.id)
        await interaction.response.edit_message(
            embed=stock_embed(interaction.guild, profile or {}),
            view=self.parent_view.rebuild(),
        )


class RoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "PokemonStockView") -> None:
        super().__init__(
            placeholder="Choose an optional alert role to ping",
            min_values=0,
            max_values=1,
            row=2,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        role_id = self.values[0].id if self.values else None
        profile = await self.parent_view.bot.database.set_stock_ping_role(interaction.guild.id, role_id)
        await interaction.response.edit_message(
            embed=stock_embed(interaction.guild, profile or {}),
            view=self.parent_view.rebuild(),
        )


class RemoveProductSelect(discord.ui.Select):
    def __init__(self, parent: "PokemonStockView", products: list[dict]) -> None:
        options = [
            discord.SelectOption(
                label=f"{item.get('store', 'Store')} — {item.get('name') or item.get('sku') or 'Product'}"[:100],
                value=str(item["_id"]),
            )
            for item in products[:25]
        ]
        super().__init__(placeholder="Choose a product to remove", options=options, min_values=1, max_values=1)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        profile = await self.parent_view.bot.database.remove_stock_product(interaction.guild.id, self.values[0])
        await interaction.response.edit_message(
            embed=stock_embed(interaction.guild, profile or {}),
            view=self.parent_view.rebuild(),
        )


class AlertSettingsView(AuthorizedView):
    def __init__(self, *, parent: "PokemonStockView", profile: dict) -> None:
        super().__init__(guild_id=parent.guild_id, requester_id=parent.requester_id)
        self.parent = parent
        self.profile = profile
        self._refresh_labels()

    def _alerts(self) -> dict:
        return self.profile.get("pokemon_stock", {}).get("alerts", {})

    def _refresh_labels(self) -> None:
        alerts = self._alerts()
        self.restocks.label = f"Restocks: {'ON' if alerts.get('in_stock', True) else 'OFF'}"
        self.sellouts.label = f"Sold Out: {'ON' if alerts.get('out_of_stock', False) else 'OFF'}"
        self.prices.label = f"Price Changes: {'ON' if alerts.get('price_change', False) else 'OFF'}"

    async def _toggle(self, interaction: discord.Interaction, name: str, current: bool) -> None:
        assert interaction.guild is not None
        self.profile = await self.parent.bot.database.set_stock_alert(interaction.guild.id, name, not current) or self.profile
        self._refresh_labels()
        await interaction.response.edit_message(content="Choose which stock changes should send alerts:", view=self)

    @discord.ui.button(label="Restocks", emoji="🟢", style=discord.ButtonStyle.success)
    async def restocks(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "in_stock", self._alerts().get("in_stock", True))

    @discord.ui.button(label="Sold Out", emoji="🔴", style=discord.ButtonStyle.danger)
    async def sellouts(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "out_of_stock", self._alerts().get("out_of_stock", False))

    @discord.ui.button(label="Price Changes", emoji="💲", style=discord.ButtonStyle.primary)
    async def prices(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._toggle(interaction, "price_change", self._alerts().get("price_change", False))


class PokemonStockView(AuthorizedView):
    def __init__(self, *, bot, guild_id: int, requester_id: int, database_connected: bool) -> None:
        super().__init__(guild_id=guild_id, requester_id=requester_id)
        self.bot = bot
        self.database_connected = database_connected
        self.add_item(ChannelSelect(self))
        self.add_item(RoleSelect(self))

    def rebuild(self) -> "PokemonStockView":
        return PokemonStockView(
            bot=self.bot,
            guild_id=self.guild_id,
            requester_id=self.requester_id,
            database_connected=self.database_connected,
        )

    @discord.ui.button(label="Add Product", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add_product(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(AddProductModal(self))

    @discord.ui.button(label="Remove Product", emoji="➖", style=discord.ButtonStyle.danger, row=0)
    async def remove_product(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.get_guild_profile(interaction.guild.id) or {}
        products = profile.get("pokemon_stock", {}).get("products", [])
        if not products:
            await interaction.response.send_message("There are no watched products to remove.", ephemeral=True)
            return
        view = AuthorizedView(guild_id=self.guild_id, requester_id=self.requester_id)
        view.add_item(RemoveProductSelect(self, products))
        await interaction.response.send_message("Select the product to remove:", view=view, ephemeral=True)

    @discord.ui.button(label="Enable / Disable", emoji="🔔", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.get_guild_profile(interaction.guild.id) or {}
        config = profile.get("pokemon_stock", {})
        enable = not bool(config.get("enabled", False))
        if enable and not config.get("channel_id"):
            await interaction.response.send_message("Choose the alert channel first.", ephemeral=True)
            return
        updated = await self.bot.database.set_stock_enabled(interaction.guild.id, enable)
        await interaction.response.edit_message(embed=stock_embed(interaction.guild, updated or {}), view=self.rebuild())

    @discord.ui.button(label="Alert Settings", emoji="⚙️", style=discord.ButtonStyle.secondary, row=3)
    async def alert_settings(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.get_guild_profile(interaction.guild.id) or {}
        await interaction.response.send_message(
            "Choose which stock changes should send alerts:",
            view=AlertSettingsView(parent=self, profile=profile),
            ephemeral=True,
        )

    @discord.ui.button(label="Check Now", emoji="🔄", style=discord.ButtonStyle.secondary, row=3)
    async def check_now(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        profile = await self.bot.database.get_guild_profile(interaction.guild.id) or {}
        products = profile.get("pokemon_stock", {}).get("products", [])
        if not products:
            await interaction.followup.send("No products are being watched yet.", ephemeral=True)
            return
        lines = []
        for item in products[:10]:
            try:
                status = await fetch_product_status(item["url"])
                state = "✅ In stock" if status.available is True else "❌ Unavailable" if status.available is False else "❔ Unknown"
                price = f" — {status.price}" if status.price else ""
                lines.append(f"**{status.store} — {status.name}** — {state}{price}")
            except Exception as exc:
                lines.append(f"**{item.get('store', 'Store')} — {item.get('name', 'Product')}** — ⚠️ {exc}")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="Dashboard", emoji="🏠", style=discord.ButtonStyle.secondary, row=3)
    async def dashboard(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        from views.dashboard import DashboardView, dashboard_embed
        assert interaction.guild is not None
        await interaction.response.edit_message(
            embed=dashboard_embed(interaction.guild, database_connected=self.database_connected),
            view=DashboardView(bot=self.bot, guild_id=self.guild_id, requester_id=self.requester_id, database_connected=self.database_connected),
        )
