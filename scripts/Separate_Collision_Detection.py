import carla
import time
import csv
import os
from datetime import datetime
from collections import defaultdict
import math

def euclidean_distance(loc1, loc2):
    return math.sqrt((loc1.x - loc2.x) ** 2 + (loc1.y - loc2.y) ** 2)

def start_collision_monitor(
    client: carla.Client,
    csv_path: str = "collision_details.csv",
    zone_radius: float = 2.0
):
    world = client.get_world()
    print("⏳ Waiting for ego vehicle to spawn...")

    existing_ids = {v.id for v in world.get_actors().filter('vehicle.*')}
    ego_vehicle = None
    collision_sensor = None
    collision_zones = []  # list of carla.Location
    collision_count = 0
    zone_active = True

    # CSV setup
    with open(csv_path, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=[
            "timestamp", "vehicle_type", "collision_location", "object_type", "object_name"
        ])
        writer.writeheader()

    try:
        # Wait for ego vehicle
        while not ego_vehicle:
            for v in world.get_actors().filter('vehicle.*'):
                if v.id not in existing_ids:
                    ego_vehicle = v
                    print(f"🚗 Ego vehicle detected: {v.type_id}, ID {v.id}")
                    break
            time.sleep(0.5)

        # Attach sensor
        bp = world.get_blueprint_library().find('sensor.other.collision')
        collision_sensor = world.spawn_actor(bp, carla.Transform(), attach_to=ego_vehicle)

        def is_in_existing_zone(new_loc):
            for zone in collision_zones:
                if euclidean_distance(new_loc, zone) < zone_radius:
                    return True
            return False

        def cleanup_zones(current_loc):
            # Remove zones if ego is far from them
            return [z for z in collision_zones if euclidean_distance(current_loc, z) < zone_radius]

        def on_collision(event):
            nonlocal collision_count, collision_zones

            loc = ego_vehicle.get_location()
            loc_str = f"({loc.x:.2f}, {loc.y:.2f}, {loc.z:.2f})"

            # Check if this collision is near any previous one
            if is_in_existing_zone(loc):
                return  # Skip duplicate

            # Register new zone
            collision_zones.append(loc)
            collision_count += 1

            # Object info
            actor = event.other_actor
            obj_type = "static"
            if "vehicle" in actor.type_id or "walker" in actor.type_id:
                obj_type = "running"

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            print(f"\n💥 Collision at {loc_str}")
            print(f"🕒 Time: {timestamp}")
            print(f"🚗 Ego Vehicle: {ego_vehicle.type_id}")
            print(f"📦 Object: {actor.type_id} ({obj_type})")

            with open(csv_path, 'a', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=[
                    "timestamp", "vehicle_type", "collision_location", "object_type", "object_name"
                ])
                writer.writerow({
                    "timestamp": timestamp,
                    "vehicle_type": ego_vehicle.type_id,
                    "collision_location": loc_str,
                    "object_type": obj_type,
                    "object_name": actor.type_id
                })

        collision_sensor.listen(on_collision)
        print(f"📡 Sensor attached to vehicle ID {ego_vehicle.id}")

        # Monitoring loop
        while True:
            # Remove old zones if ego moved away
            loc = ego_vehicle.get_location()
            collision_zones = cleanup_zones(loc)

            active_ids = {v.id for v in world.get_actors().filter('vehicle.*')}
            if ego_vehicle.id not in active_ids:
                print("🛑 Vehicle removed. Exiting.")
                break
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("🛑 Interrupted manually.")
    finally:
        if collision_sensor:
            collision_sensor.stop()
            collision_sensor.destroy()

        print(f"\n📊 Total collisions detected: {collision_count}")
        with open(csv_path, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([])
            writer.writerow(["TOTAL_COLLISIONS", collision_count])
        print(f"📝 Summary saved to CSV: {csv_path}")
client = carla.Client("localhost", 2000)
client.set_timeout(10.0)

start_collision_monitor(
    client=client,
    csv_path="collision_log_radius.csv"
)