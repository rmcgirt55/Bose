import asyncio
from bleak import BleakClient
from sleepbuds.controller import SleepbudsController

async def main():
    addresses = {}

    with open(".sleepbuds_addresses") as file:
        for line in file:
            if "=" in line:
                name, address = line.strip().split("=", 1)
                addresses[name] = address

    required = ("Bose Sleepbuds L", "Bose Sleepbuds R")

    if not all(name in addresses for name in required):
        raise SystemExit("ERROR: Both Sleepbuds addresses required")

    failed = False

    for name in required:
        try:
            async with BleakClient(addresses[name], timeout=20) as client:
                controller = SleepbudsController(client)
                success = await controller.stop_playback()

                print(f"{name}: {'Stop sent' if success else 'FAILED'}")

                if not success:
                    failed = True

        except Exception as error:
            print(f"{name}: FAILED ({error})")
            failed = True

    if failed:
        raise SystemExit(1)

    print("SUCCESS: Stop commands sent to both earbuds")

if __name__ == "__main__":
    asyncio.run(main())
