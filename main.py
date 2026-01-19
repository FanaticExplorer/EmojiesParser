import asyncio
import json
import time
from functools import partial
from pathlib import Path
import io
import re

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
DOWNLOAD_THREADS = 5

def save_png_from_image(image, file_path):
    """Helper function to save a PIL Image as PNG with proper mode conversion."""
    if image.mode in ('RGBA', 'LA'):
        image.save(file_path, 'PNG', optimize=True)
    else:
        # Convert to RGBA to preserve any transparency
        image_rgb = image.convert('RGBA')
        image_rgb.save(file_path, 'PNG', optimize=True)

def save_error_log(error_log_file: Path, errors: list, item_type: str):
    """Helper function to save errors to a log file."""
    if errors:
        with open(error_log_file, 'w') as f:
            f.write(f"{item_type} download errors ({len(errors)} total):\n")
            f.write("=" * 50 + "\n")
            for error in errors:
                f.write(f"{error}\n")
        console.print(f"[yellow]⚠️  Error log saved to: {error_log_file}[/yellow]")

def sanitize_filename(filename):
    """Sanitize filename for Windows compatibility."""
    # Remove or replace invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove trailing dots and spaces
    filename = filename.rstrip('. ')
    # Ensure it's not empty
    if not filename:
        filename = 'unnamed'
    return filename

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
        async def capture_emojies_request(tab_arg, event, output_file_arg):
            url = event['params']['response']['url']
            if 'nelly.tools/api/lookup/guild-followup/' in url:
                request_id = event['params']['requestId']
                await asyncio.sleep(1)
                body = await tab_arg.get_network_response_body(request_id)

                # Save output to file with 4-space formatting
                response_data = json.loads(body)
                with open(output_file_arg, 'w', encoding='utf-8') as response_f:
                    json.dump(response_data, response_f, indent=4, ensure_ascii=False)
                console.print(f"✅ Guild data saved to: {output_file_arg}", style="green")

                request_captured.set()

        await tab.enable_network_events()
        await tab.on(NetworkEvent.RESPONSE_RECEIVED, partial(capture_emojies_request, tab, output_file_arg=output_file))

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

async def download_emojis(response_file: Path, max_concurrent=DOWNLOAD_THREADS):
    # Load emoji data
    with open(response_file, 'r', encoding='utf-8') as f:
        emojis = json.load(f)['data']['emojis']

    total_emojis = len(emojis)
    console.print(f"[cyan]Found {total_emojis} emojis - Starting download...[/cyan]")

    # Setup download folder
    download_folder = response_file.parent / "emojis"
    download_folder.mkdir(parents=True, exist_ok=True)

    # Setup error logging
    error_log_file = response_file.parent / "emoji_errors.log"
    errors = []

    downloaded_count = 0
    failed_count = 0
    semaphore = asyncio.Semaphore(max_concurrent)
    start_time = time.time()

    # Track used filenames to handle duplicates
    used_filenames = set()

    def get_unique_filename(base_name, extension):
        """Generate a unique filename by adding numbers if needed."""
        filename = f"{base_name}{extension}"
        if filename not in used_filenames:
            used_filenames.add(filename)
            return filename

        counter = 1
        while True:
            filename = f"{base_name}{counter}{extension}"
            if filename not in used_filenames:
                used_filenames.add(filename)
                return filename
            counter += 1

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

        async def download_single_emoji(session_arg, emoji):
            nonlocal downloaded_count, failed_count

            async with semaphore:
                name = emoji['name']
                emoji_id = emoji['id']
                is_animated = emoji.get('animated', False)

                progress.update(task, description=f"Downloading :{name}:")

                # Try to download with the expected format first
                initial_extension = '.gif' if is_animated else '.webp'
                url = f"https://cdn.discordapp.com/emojis/{emoji_id}{initial_extension}"

                try:
                    async with session_arg.get(url) as response:
                        if response.status == 200:
                            content = await response.read()
                            # Get unique filename
                            filename = get_unique_filename(name, initial_extension)
                            file_path = download_folder / filename
                            file_path.write_bytes(content)
                            downloaded_count += 1
                            progress.update(task, advance=1)
                        elif response.status == 415 and is_animated:
                            # Discord sometimes marks emoji as animated but it's actually webp
                            # Try downloading as webp instead
                            webp_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.webp"
                            async with session_arg.get(webp_url) as webp_response:
                                if webp_response.status == 200:
                                    content = await webp_response.read()
                                    # Get unique filename with webp extension
                                    filename = get_unique_filename(name, '.webp')
                                    file_path = download_folder / filename
                                    file_path.write_bytes(content)
                                    downloaded_count += 1
                                    progress.update(task, advance=1)
                                else:
                                    error_msg = f"Failed to download emoji '{name}' (ID: {emoji_id}): HTTP {webp_response.status} (tried both .gif and .webp) - {url}"
                                    errors.append(error_msg)
                                    failed_count += 1
                                    progress.update(task, advance=1)
                        else:
                            error_msg = f"Failed to download emoji '{name}' (ID: {emoji_id}): HTTP {response.status} - {url}"
                            errors.append(error_msg)
                            failed_count += 1
                            progress.update(task, advance=1)
                except Exception as e:
                    error_msg = f"Exception downloading emoji '{name}' (ID: {emoji_id}): {str(e)} - {url}"
                    errors.append(error_msg)
                    failed_count += 1
                    progress.update(task, advance=1)

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

    # Save errors to file if any occurred
    save_error_log(error_log_file, errors, "Emoji")

    completion_msg = f"✅ [green]Downloaded {downloaded_count} emojis in {elapsed_time:.1f}s[/green]"
    if failed_count > 0:
        completion_msg += f" [red]({failed_count} failed)[/red]"

    console.print(completion_msg)

async def download_stickers(response_file: Path, max_concurrent=DOWNLOAD_THREADS):
    # Load sticker data
    with open(response_file, 'r', encoding='utf-8') as f:
        stickers = json.load(f)['data']['stickers']

    total_stickers = len(stickers)
    console.print(f"[cyan]Found {total_stickers} stickers - Starting download...[/cyan]")

    # Setup download folder
    download_folder = response_file.parent / "stickers"
    download_folder.mkdir(parents=True, exist_ok=True)

    # Setup error logging
    error_log_file = response_file.parent / "sticker_errors.log"
    errors = []

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

        async def download_single_sticker(session_arg, sticker):
            nonlocal downloaded_count, failed_count

            async with semaphore:
                name = sticker['name']
                sticker_id = sticker['id']

                # Sanitize filename to avoid Windows invalid characters
                safe_name = sanitize_filename(name)

                # Build URL for stickers
                url = f"https://media.discordapp.net/stickers/{sticker_id}.png"
                progress.update(task, description=f"Downloading: {name}")

                try:
                    async with session_arg.get(url) as response:
                        if response.status == 200:
                            image_bytes = await response.read()

                            # Process with Pillow
                            try:
                                with Image.open(io.BytesIO(image_bytes)) as im:
                                    # Check if animated using proper method
                                    try:
                                        is_animated = getattr(im, 'is_animated', False) and im.n_frames > 1
                                    except (AttributeError, OSError):
                                        is_animated = False

                                    if not is_animated:
                                        # Static PNG
                                        file_path = download_folder / f"{safe_name}.png"
                                        # Convert to RGB if necessary to ensure PNG compatibility
                                        save_png_from_image(im, file_path)
                                    else:
                                        # Animated APNG - convert to GIF
                                        frames = []
                                        durations = []
                                        try:
                                            for frame_num in range(im.n_frames):
                                                im.seek(frame_num)
                                                frame = im.copy()
                                                # Convert to RGBA for consistency
                                                if frame.mode != 'RGBA':
                                                    frame = frame.convert('RGBA')
                                                frames.append(frame)
                                                # Get duration, default to 100ms if not available
                                                duration = im.info.get('duration', 100)
                                                durations.append(duration)

                                            file_path = download_folder / f"{safe_name}.gif"
                                            # Save as GIF with proper optimization
                                            frames[0].save(
                                                file_path,
                                                format='GIF',
                                                save_all=True,
                                                append_images=frames[1:],
                                                duration=durations,
                                                loop=0,
                                                optimize=True,
                                                disposal=2  # Clear frame before next
                                            )
                                        except (IOError, OSError, ValueError):
                                            # If GIF conversion fails, save as static PNG
                                            file_path = download_folder / f"{safe_name}.png"
                                            im.seek(0)  # Go to first frame
                                            frame = im.copy()
                                            save_png_from_image(frame, file_path)

                                downloaded_count += 1
                            except (IOError, OSError, ValueError):
                                # If PIL processing fails, save raw bytes as PNG
                                file_path = download_folder / f"{safe_name}.png"
                                file_path.write_bytes(image_bytes)
                                downloaded_count += 1
                        else:
                            error_msg = f"Failed to download sticker '{name}' (ID: {sticker_id}): HTTP {response.status} - {url}"
                            errors.append(error_msg)
                            failed_count += 1

                    progress.update(task, advance=1)
                except Exception as e:
                    error_msg = f"Exception downloading sticker '{name}' (ID: {sticker_id}): {str(e)} - {url}"
                    errors.append(error_msg)
                    failed_count += 1
                    progress.update(task, advance=1)

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

    # Save errors to file if any occurred
    save_error_log(error_log_file, errors, "Sticker")

    completion_msg = f"✅ [green]Downloaded {downloaded_count} stickers in {elapsed_time:.1f}s[/green]"
    if failed_count > 0:
        completion_msg += f" [red]({failed_count} failed)[/red]"

    console.print(completion_msg)



if __name__ == "__main__":
    console.print(Panel("🎭 [bold cyan]Discord Emoji Downloader[/bold cyan] 🎭", expand=False))
    console.print(f"[yellow]Please enter amount of threads to use (default {DOWNLOAD_THREADS}):[/yellow] ",
                  end="")
    threads_input = input().strip()
    if threads_input.isdigit() and int(threads_input) > 0:
        DOWNLOAD_THREADS = int(threads_input)
    console.print("[yellow]Please enter guild invite code (or ID):[/yellow] ", end="")
    guild_invite = input().strip()
    if '/' in guild_invite:
        guild_invite = guild_invite.rstrip('/').split('/')[-1]

    output_json_file = Path(f"output/{guild_invite}/response.json")

    if not output_json_file.exists():
        with console.status(f"[blue]Fetching data for guild: {guild_invite}[/blue]", spinner="dots"):
            asyncio.run(get_media(guild_invite))
    else:
        console.print(f"[green]Using existing guild data from: {output_json_file}[/green]")

    asyncio.run(download_emojis(output_json_file, DOWNLOAD_THREADS))
    asyncio.run(download_stickers(output_json_file, DOWNLOAD_THREADS))
