import asyncio
import json
import time
from functools import partial
from pathlib import Path
import io

import aiohttp
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
from pydoll.protocol.network.events import NetworkEvent
from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, SpinnerColumn, TimeElapsedColumn
from rich.panel import Panel
from rich.table import Column
from PIL import Image

console = Console()

async def get_media(guild: str):
    options = ChromiumOptions()
    options.add_argument('--headless=new')
    options.add_argument('--start-maximized')
    options.add_argument('--disable-notifications')
    options.add_argument('--disable-blink-features=AutomationControlled')

    # Create output directory and file path
    output_dir = Path(f"output/{guild}")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "response.json"

    async with Chrome(options=options) as browser:
        tab = await browser.start()
        # Go to the guild lookup page
        await tab.go_to('https://nelly.tools/lookup/guild')
        await asyncio.sleep(2)

        # Create an event to signal when the request has been captured
        request_captured = asyncio.Event()

        # Get ready to monitor the requests
        # noinspection PyShadowingNames
        async def capture_emojies_request(tab, event):
            url = event['params']['response']['url']
            if 'nelly.tools/api/lookup/guild-followup/' in url:
                request_id = event['params']['requestId']
                await asyncio.sleep(1)
                body = await tab.get_network_response_body(request_id)

                # Save output to file with 4-space formatting
                response_data = json.loads(body)
                with open(output_file, 'w') as f:
                    json.dump(response_data, f, indent=4)
                console.print(f"✅ Guild data saved to: {output_file}", style="green")

                request_captured.set()

        await tab.enable_network_events()
        await tab.on(NetworkEvent.RESPONSE_RECEIVED, partial(capture_emojies_request, tab))

        # Inputting the info
        input_field = await tab.find(id="inputVal")
        await input_field.type_text(guild, humanize=True)
        await asyncio.sleep(1)
        submit_button = await tab.find(text="Check")
        await submit_button.click()

        # Wait for the request to be captured with a 60-second timeout
        try:
            await asyncio.wait_for(request_captured.wait(), timeout=60)
        except asyncio.TimeoutError:
            console.print("⏰ Timeout: Request was not captured within 60 seconds", style="red")

async def download_emojis(response_file: Path, max_concurrent=5):
    # Load emoji data
    with open(response_file, 'r') as f:
        emojis = json.load(f)['data']['emojis']

    total_emojis = len(emojis)
    console.print(f"[cyan]Found {total_emojis} emojis - Starting download...[/cyan]")

    # Setup download folder
    download_folder = response_file.parent / "emojis"
    download_folder.mkdir(parents=True, exist_ok=True)

    downloaded_count = 0
    failed_count = 0
    semaphore = asyncio.Semaphore(max_concurrent)
    start_time = time.time()

    # Create progress bar
    with Progress(
        SpinnerColumn(spinner_name="bouncingBar", style="cyan"),
        TextColumn("[progress.description]{task.description}", table_column=Column(width=30, no_wrap=True)),
        BarColumn(complete_style="cyan"),
        TextColumn("[blue]{task.completed}/{task.total}[/blue]"),
        TimeElapsedColumn(),
        console=console,
        transient=True
    ) as progress:
        task = progress.add_task("Downloading: ", total=total_emojis)

        async def download_single_emoji(session, emoji):
            nonlocal downloaded_count, failed_count

            async with semaphore:
                name = emoji['name']
                emoji_id = emoji['id']
                is_animated = emoji.get('animated', False)

                # Build URL and file path
                extension = '.gif' if is_animated else '.webp'
                url = f"https://cdn.discordapp.com/emojis/{emoji_id}{extension}"
                file_path = download_folder / f"{name}{extension}"

                # Prepare a truncated, safe description for the progress bar
                display_name = f"{name}{extension}"
                progress.update(task, description=f"Downloading: {display_name}")

                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            content = await response.read()
                            file_path.write_bytes(content)
                            downloaded_count += 1
                            progress.update(task, advance=1, description="Downloading...")
                        else:
                            failed_count += 1
                            progress.update(task, advance=1, description="Downloading...")
                except Exception as e:
                    failed_count += 1
                    progress.update(task, advance=1, description="Downloading...")

        # Create optimized session
        connector = aiohttp.TCPConnector(
            limit=max_concurrent * 2,
            limit_per_host=max_concurrent,
            ttl_dns_cache=300
        )

        timeout = aiohttp.ClientTimeout(total=15, connect=5)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            tasks = [download_single_emoji(session, emoji) for emoji in emojis]
            await asyncio.gather(*tasks, return_exceptions=True)

    # Simple completion message
    elapsed_time = time.time() - start_time

    completion_msg = f"✅ [green]Downloaded {downloaded_count} emojis in {elapsed_time:.1f}s[/green]"
    if failed_count > 0:
        completion_msg += f" [red]({failed_count} failed)[/red]"

    console.print(completion_msg)

async def download_stickers(response_file: Path, max_concurrent=5):
    # Load sticker data
    with open(response_file, 'r') as f:
        stickers = json.load(f)['data']['stickers']

    total_stickers = len(stickers)
    console.print(f"[cyan]Found {total_stickers} stickers - Starting download...[/cyan]")

    # Setup download folder
    download_folder = response_file.parent / "stickers"
    download_folder.mkdir(parents=True, exist_ok=True)

    downloaded_count = 0
    failed_count = 0
    semaphore = asyncio.Semaphore(max_concurrent)
    start_time = time.time()

    # Create progress bar
    with Progress(
            SpinnerColumn(spinner_name="bouncingBar", style="cyan"),
            TextColumn("[progress.description]{task.description}", table_column=Column(width=30, no_wrap=True)),
            BarColumn(complete_style="cyan"),
            TextColumn("[blue]{task.completed}/{task.total}[/blue]"),
            TimeElapsedColumn(),
            console=console,
            transient=True
    ) as progress:
        task = progress.add_task("Downloading: ", total=total_stickers)

        async def download_single_sticker(session, sticker):
            nonlocal downloaded_count, failed_count

            async with semaphore:
                name = sticker['name']
                sticker_id = sticker['id']

                # Build URL for stickers
                url = f"https://media.discordapp.net/stickers/{sticker_id}.png"
                progress.update(task, description=f"Downloading: {name}")

                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            image_bytes = await response.read()

                            # Process with Pillow
                            with Image.open(io.BytesIO(image_bytes)) as im:
                                is_animated = getattr(im, 'is_animated', False)

                                if not is_animated:
                                    # Static PNG
                                    file_path = download_folder / f"{name}.png"
                                    im.save(file_path, 'PNG')
                                else:
                                    # Animated APNG - convert to GIF
                                    frames = []
                                    durations = []
                                    for frame_num in range(im.n_frames):
                                        im.seek(frame_num)
                                        frames.append(im.copy())
                                        durations.append(im.info.get('duration', 100))

                                    file_path = download_folder / f"{name}.gif"
                                    save_kwargs = {
                                        'format': 'GIF',
                                        'save_all': True,
                                        'append_images': frames[1:],
                                        'duration': durations,
                                        'loop': 0,
                                        'optimize': True,
                                    }
                                    if 'transparency' in im.info:
                                        save_kwargs['transparency'] = im.info['transparency']
                                    frames[0].save(file_path, **save_kwargs)

                            downloaded_count += 1
                        else:
                            failed_count += 1

                    progress.update(task, advance=1, description="Downloading...")
                except Exception as e:
                    failed_count += 1
                    progress.update(task, advance=1, description="Downloading...")

        # Create optimized session
        connector = aiohttp.TCPConnector(
            limit=max_concurrent * 2,
            limit_per_host=max_concurrent,
            ttl_dns_cache=300
        )

        timeout = aiohttp.ClientTimeout(total=15, connect=5)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            tasks = [download_single_sticker(session, sticker) for sticker in stickers]
            await asyncio.gather(*tasks, return_exceptions=True)

    # Simple completion message
    elapsed_time = time.time() - start_time

    completion_msg = f"✅ [green]Downloaded {downloaded_count} stickers in {elapsed_time:.1f}s[/green]"
    if failed_count > 0:
        completion_msg += f" [red]({failed_count} failed)[/red]"

    console.print(completion_msg)



if __name__ == "__main__":
    console.print(Panel("🎭 [bold cyan]Discord Emoji Downloader[/bold cyan] 🎭", expand=False))
    console.print("[yellow]Please enter guild invite code:[/yellow] ", end="")
    guild_invite = input().strip()

    output_file = Path(f"output/{guild_invite}/response.json")

    if not output_file.exists():
        console.print(f"[blue]Fetching data for guild: {guild_invite}[/blue]")
        asyncio.run(get_media(guild_invite))
    else:
        console.print(f"[green]Using existing guild data from: {output_file}[/green]")

    asyncio.run(download_emojis(output_file))
    asyncio.run(download_stickers(output_file))
