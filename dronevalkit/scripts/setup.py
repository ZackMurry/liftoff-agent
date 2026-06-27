import asyncio
from mavsdk import System
from mavsdk.offboard import PositionNedYaw, OffboardError

async def test():
    drone = System()
    await drone.connect(system_address='udp://:14540')
    
    print('Waiting for connection...')
    async for state in drone.core.connection_state():
        if state.is_connected:
            print('Connected!')
            break
    
    print('Waiting for GPS...')
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            print('GPS ready!')
            break
    
    # NED: North (m), East (m), Down (m), Yaw (deg)
    # Down is negative = up
    
    # Set initial setpoint before starting offboard
    await drone.offboard.set_position_ned(PositionNedYaw(0, 0, -10, 0))
    
    print('Arming...')
    await drone.action.arm()
    
    print('Starting offboard...')
    await drone.offboard.start()
    
    # Take off to 10m
    print('Taking off to 10m...')
    await drone.offboard.set_position_ned(PositionNedYaw(0, 0, -10, 0))
    await asyncio.sleep(8)
    
    # Fly 20m north at 10m altitude
    print('Flying 20m north...')
    await drone.offboard.set_position_ned(PositionNedYaw(20, 0, -10, 0))
    await asyncio.sleep(8)
    
    # Fly 20m east
    print('Flying 20m east...')
    await drone.offboard.set_position_ned(PositionNedYaw(20, 20, -10, 0))
    await asyncio.sleep(8)
    
    # Back to origin
    print('Returning to start...')
    await drone.offboard.set_position_ned(PositionNedYaw(0, 0, -10, 0))
    await asyncio.sleep(8)
    
    # Land
    print('Landing...')
    await drone.action.land()
    await asyncio.sleep(8)
    
    print('Done!')

asyncio.run(test())
