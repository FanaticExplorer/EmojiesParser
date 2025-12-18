import asyncio
import json
from functools import partial
from pathlib import Path

from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
from pydoll.protocol.network.events import NetworkEvent


async def get_emojis_dict(guild_invite: str):
    options = ChromiumOptions()
    options.add_argument('--headless=new')
    options.add_argument('--start-maximized')
    options.add_argument('--disable-notifications')
    options.add_argument('--disable-blink-features=AutomationControlled')

    # Create output directory and file path
    output_dir = Path(f"output/{guild_invite}")
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
                print(body)

                # Save output to file with 4-space formatting
                response_data = json.loads(body)
                with open(output_file, 'w') as f:
                    json.dump(response_data, f, indent=4)
                print(f"Output saved to: {output_file}")

                request_captured.set()

        await tab.enable_network_events()
        await tab.on(NetworkEvent.RESPONSE_RECEIVED, partial(capture_emojies_request, tab))

        # Inputting the info
        input_field = await tab.find(id="inputVal")
        await input_field.type_text(guild_invite, humanize=True)
        await asyncio.sleep(1)
        submit_button = await tab.find(text="Check")
        await submit_button.click()

        # Wait for the request to be captured with a 60-second timeout
        try:
            await asyncio.wait_for(request_captured.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("Timeout: Request was not captured within 60 seconds")

if __name__ == "__main__":
    asyncio.run(get_emojis_dict("archlinux")) # Replace with your desired guild invite code