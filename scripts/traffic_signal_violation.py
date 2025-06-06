import carla
import time
import csv
import os
import math
from datetime import datetime

# 1. CONNECT TO CARLA 
client = carla.Client("localhost", 2000)
client.set_timeout(10.0)
world = client.get_world() 

# 2. FIND THE VEHICLE TO MONITOR
vehicle = None
for actor in world.get_actors().filter("vehicle.*"):
    if actor.attributes.get("role_name", "") == "hero":
        vehicle = actor
        break

if vehicle is None:
    vehicles = world.get_actors().filter("vehicle.*")
    if not vehicles:
        raise RuntimeError("No vehicle found. Please spawn one first.")
    vehicle = vehicles[0]

print(f"Monitoring vehicle ID={vehicle.id} ({vehicle.type_id})")

# 3. CSV LOGGING SETUP
csv_filename = "violations.csv"
if not os.path.exists(csv_filename):
    with open(csv_filename, mode="w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Timestamp", "VehicleID", "Speed_mps",
            "Location_x", "Location_y", "Location_z",
            "TrafficLightState", "ViolationType", "DistancePastStop"
        ])

# 4. UTILITY FUNCTIONS
def get_speed(veh):
    # Return speed (m/s) of vehicle.
    v = veh.get_velocity()
    return math.sqrt(v.x**2 + v.y**2 + v.z**2)

def get_stop_data(traffic_light):
    # Return a tuple (stop_location, stop_forward_vector) for the first stop waypoint. If no waypoint is available, return (None, None).
    stop_waypoints = traffic_light.get_stop_waypoints()
    if stop_waypoints:
        swp = stop_waypoints[0]  # use the first stop waypoint
        swp_loc = swp.transform.location
        swp_rot = swp.transform.rotation
        forward = swp_rot.get_forward_vector()
        return swp_loc, forward
    return None, None

def is_inside_trigger_box(veh, traffic_light):
    # Fallback check: Return True if vehicle's location is inside the TL trigger volume.
    trigger = traffic_light.trigger_volume

    # Convert local trigger box center into world coordinates
    world_trigger_loc = traffic_light.get_transform().transform(trigger.location)
    veh_loc = veh.get_location()
    dx = abs(world_trigger_loc.x - veh_loc.x)
    dy = abs(world_trigger_loc.y - veh_loc.y)
    dz = abs(world_trigger_loc.z - veh_loc.z)
    return (dx <= trigger.extent.x and dy <= trigger.extent.y and dz <= trigger.extent.z)

# 5. MAIN MONITOR LOOP
violation_logged = False

try:
    while True:
        # 5.1. Find the traffic light (if any) that affects this vehicle
        tl = vehicle.get_traffic_light()

        if tl is not None:
            state = tl.get_state()
            veh_loc = vehicle.get_location()
            speed = get_speed(vehicle)

            # 5.2. Attempt to get the stop waypoint location & direction
            stop_loc, stop_fwd = get_stop_data(tl)

            violation_detected = False
            violation_type = None
            distance_past = None

            if state == carla.TrafficLightState.Red:
                if stop_loc is not None:
                    # Compute vector from stop line to vehicle, then project onto lane forward
                    rel_x = veh_loc.x - stop_loc.x
                    rel_y = veh_loc.y - stop_loc.y
                    rel_z = veh_loc.z - stop_loc.z
                    # Dot product with forward vector: if positive, vehicle is "past" the stop line
                    dot = rel_x * stop_fwd.x + rel_y * stop_fwd.y + rel_z * stop_fwd.z

                    # If dot > 0, we've crossed the stop line in the direction of travel
                    if dot > 0 and speed > 1.0:
                        violation_detected = True
                        violation_type = "StopWaypointPassed"
                        distance_past = dot  # how far “beyond” the stop line

                else:
                    # No stop waypoint available: fall back to trigger‐volume check
                    inside_trigger = is_inside_trigger_box(vehicle, tl)
                    if inside_trigger and speed > 1.0:
                        violation_detected = True
                        violation_type = "TriggerVolume"
                        # We can record distance to TL center as a rough proxy
                        distance_past = veh_loc.distance(tl.get_transform().location)

            # 5.3. Log if first time this red‐state violation is seen
            if violation_detected and not violation_logged:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"Red light violation at {timestamp} | "
                      f"Vehicle ID={vehicle.id}, Speed={speed:.2f} m/s, "
                      f"Type={violation_type}, DistancePast={distance_past:.2f}")

                # Write a row to CSV
                with open(csv_filename, mode="a", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        timestamp,
                        vehicle.id,
                        f"{speed:.2f}",
                        f"{veh_loc.x:.2f}",
                        f"{veh_loc.y:.2f}",
                        f"{veh_loc.z:.2f}",
                        str(state),
                        violation_type,
                        f"{distance_past:.2f}"
                    ])
                violation_logged = True

            elif not violation_detected:
                # Reset flag once the vehicle either slows down, goes back before stop line,
                # or the light turns green
                violation_logged = False
        else:
            # Vehicle not currently influenced by any TL
            violation_logged = False

        # 5.4. Small delay → 10 Hz polling
        time.sleep(0.1)

except KeyboardInterrupt:
    print("\nMonitoring stopped by user (Ctrl+C).")

except Exception as e:
    print(f"\nException occurred: {e}")

finally:
    print("Exiting async red-light violation monitor.")
