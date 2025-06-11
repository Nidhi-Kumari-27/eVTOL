import carla
import time
import csv
import os
import math
import sys
from collections import defaultdict
from datetime import datetime

# --- Configuration Constants ---
ZONE_RADIUS = 2.0

def find_violation_detection_folder():
    """
    Cross-platform: Creates an 'output/violation_detection' folder under the current working directory.
    """
    base_dir = os.path.join(os.getcwd(), "output", "violation_detection")
    os.makedirs(base_dir, exist_ok=True)
    return base_dir

VIOLATION_DIR = find_violation_detection_folder()

def update_master_csv(team_name, lane, collision, redlight):
    master_path = os.path.join(VIOLATION_DIR, "master_violation.csv")
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    fieldnames = [
        "team name", "date/time",
        "current_lane", "lowest_lane",
        "current_collision", "lowest_collision",
        "current_redlight", "lowest_redlight",
        "current_total", "lowest_total"
    ]

    rows = []
    if os.path.exists(master_path):
        with open(master_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            if set(reader.fieldnames or []) == set(fieldnames):
                rows = list(reader)

    updated = False
    current_total = lane + collision + redlight
    for row in rows:
        if row["team name"].strip().lower() == team_name.lower():
            updated = True
            row.update({
                "date/time": now,
                "current_lane": str(lane),
                "lowest_lane": str(min(int(row["lowest_lane"]), lane)),
                "current_collision": str(collision),
                "lowest_collision": str(min(int(row["lowest_collision"]), collision)),
                "current_redlight": str(redlight),
                "lowest_redlight": str(min(int(row["lowest_redlight"]), redlight)),
            })
            row["current_total"] = str(current_total)
            row["lowest_total"] = str(min(int(row["lowest_total"]), current_total))
            break

    if not updated:
        rows.append({
            "team name": team_name,
            "date/time": now,
            "current_lane": str(lane),
            "lowest_lane": str(lane),
            "current_collision": str(collision),
            "lowest_collision": str(collision),
            "current_redlight": str(redlight),
            "lowest_redlight": str(redlight),
            "current_total": str(current_total),
            "lowest_total": str(current_total)
        })

    with open(master_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

class CollisionMonitor:
    def __init__(self, vehicle, world):
        self.vehicle = vehicle
        self.world = world
        self.sensor = None
        self.zones = []
        self.static_count = 0
        self.dynamic_count = 0

    def attach_sensor(self):
        bp = self.world.get_blueprint_library().find("sensor.other.collision")
        self.sensor = self.world.spawn_actor(bp, carla.Transform(), attach_to=self.vehicle)
        self.sensor.listen(self._on_collision)
        print("📡 Collision sensor attached.")

    def _on_collision(self, event):
        loc = self.vehicle.get_location()
        self._cleanup_zones(loc)
        if any(math.hypot(loc.x - z.x, loc.y - z.y) < ZONE_RADIUS for z in self.zones):
            return
        other = event.other_actor
        if "vehicle" in other.type_id:
            self.dynamic_count += 1
            print(f"💥 Dynamic collision with {other.type_id}")
        else:
            self.static_count += 1
            print(f"💥 Static collision with {other.type_id}")
        self.zones.append(loc)

    def _cleanup_zones(self, current_loc):
        self.zones = [z for z in self.zones if math.hypot(current_loc.x - z.x, current_loc.y - z.y) < ZONE_RADIUS]

    def destroy(self):
        if self.sensor:
            self.sensor.stop()
            self.sensor.destroy()

class LaneMonitor:
    def __init__(self, vehicle, world):
        self.vehicle = vehicle
        self.world = world
        self.sensor = None
        self.last_turn_time = 0
        self.violations = defaultdict(int)

    def attach_sensor(self):
        bp = self.world.get_blueprint_library().find("sensor.other.lane_invasion")
        self.sensor = self.world.spawn_actor(bp, carla.Transform(), attach_to=self.vehicle)
        self.sensor.listen(self._on_lane_violation)
        print("📡 Lane Detection sensor attached.")

    def _on_lane_violation(self, event):
        markings = [m.type for m in event.crossed_lane_markings]
        now = time.time()
        if now - self.last_turn_time < 5.0:
            return
        if carla.LaneMarkingType.SolidSolid in markings:
            self.violations["illegal_double_solid_cross"] += 1
            print("ILLEGAL: Crossed DOUBLE SOLID line!")
        elif carla.LaneMarkingType.Solid in markings:
            self.violations["illegal_solid_cross"] += 1
            print("ILLEGAL: Crossed SOLID line!")
        elif carla.LaneMarkingType.Broken in markings:
            self.violations["unjustified_dashed_cross"] += 1
            print("UNJUSTIFIED: Crossed dashed line!")

    def update_turn_status(self):
        loc = self.vehicle.get_transform().location
        wp = self.world.get_map().get_waypoint(loc)
        if wp.is_junction:
            self.last_turn_time = time.time()

    def destroy(self):
        if self.sensor:
            self.sensor.stop()
            self.sensor.destroy()

class RedLightMonitor:
    def __init__(self, vehicle):
        self.vehicle = vehicle
        self.violation_logged = False
        self.violations = defaultdict(int)

    def get_speed(self):
        v = self.vehicle.get_velocity()
        return math.sqrt(v.x**2 + v.y**2 + v.z**2)

    def get_stop_data(self, tl):
        wps = tl.get_stop_waypoints()
        if wps:
            wp = wps[0]
            return wp.transform.location, wp.transform.rotation.get_forward_vector()
        return None, None

    def is_inside_trigger_box(self, tl):
        trg = tl.trigger_volume
        world_loc = tl.get_transform().transform(trg.location)
        veh_loc = self.vehicle.get_location()
        return (
            abs(world_loc.x - veh_loc.x) <= trg.extent.x and
            abs(world_loc.y - veh_loc.y) <= trg.extent.y and
            abs(world_loc.z - veh_loc.z) <= trg.extent.z
        )

    def tick(self):
        tl = self.vehicle.get_traffic_light()
        if tl is None or tl.get_state() != carla.TrafficLightState.Red:
            self.violation_logged = False
            return

        speed = self.get_speed()
        veh_loc = self.vehicle.get_location()
        stop_loc, stop_fwd = self.get_stop_data(tl)

        violation = None
        distance = 0.0
        if stop_loc:
            rel = veh_loc - stop_loc
            dot = rel.x*stop_fwd.x + rel.y*stop_fwd.y + rel.z*stop_fwd.z
            if dot > 0 and speed > 1.0:
                violation, distance = "StopWaypointPassed", dot
        elif self.is_inside_trigger_box(tl) and speed > 1.0:
            violation = "TriggerVolume"
            distance = veh_loc.distance(tl.get_transform().location)

        if violation and not self.violation_logged:
            self.violations[violation] += 1
            print(f"🚦 RED LIGHT VIOLATION: {violation} | Distance: {distance:.2f}")
            self.violation_logged = True
        elif not violation:
            self.violation_logged = False

class TeamMonitor:
    def __init__(self, client, team_name):
        self.client = client
        self.world = client.get_world()
        self.team_name = team_name.lower().replace(" ", "_")
        self.vehicle = None

        os.makedirs(VIOLATION_DIR, exist_ok=True)
        self.team_csv = os.path.join(VIOLATION_DIR, f"{self.team_name}.csv")
        self._init_team_csv()

        self.lane = None
        self.collision = None
        self.redlight = None

    def _init_team_csv(self):
        if not os.path.exists(self.team_csv):
            with open(self.team_csv, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow([
                    "illegal_double_solid_cross","illegal_solid_cross","unjustified_dashed_cross",
                    "total_lane_violations","static_collisions","dynamic_collisions","total_collisions",
                    "redlight_StopWaypointPassed","redlight_TriggerVolume","total_redlight_violations","timestamp"
                ])

    def find_vehicle(self):
        existing = {v.id for v in self.world.get_actors().filter("vehicle.*")}
        print("⏳ Waiting for ego vehicle...")
        while not self.vehicle:
            for v in self.world.get_actors().filter("vehicle.*"):
                if v.id not in existing:
                    self.vehicle = v
                    print(f"🚗 Detected ego: {v.type_id} (ID {v.id})")
                    return
            time.sleep(0.5)

    def run(self):
        self.find_vehicle()
        self.lane = LaneMonitor(self.vehicle, self.world)
        self.collision = CollisionMonitor(self.vehicle, self.world)
        self.redlight = RedLightMonitor(self.vehicle)

        self.lane.attach_sensor()
        self.collision.attach_sensor()

        try:
            while True:
                self.lane.update_turn_status()
                self.redlight.tick()
                self.collision._cleanup_zones(self.vehicle.get_location())
                if self.vehicle.id not in {v.id for v in self.world.get_actors().filter("vehicle.*")}:
                    break
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()

    def cleanup(self):
        ds = self.lane.violations["illegal_double_solid_cross"]
        s = self.lane.violations["illegal_solid_cross"]
        d = self.lane.violations["unjustified_dashed_cross"]
        total_lane = ds + s + d
        static = self.collision.static_count
        dynamic = self.collision.dynamic_count
        total_coll = static + dynamic
        stop_v = self.redlight.violations["StopWaypointPassed"]
        trig_v = self.redlight.violations["TriggerVolume"]
        total_red = stop_v + trig_v

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.team_csv, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([ds, s, d, total_lane, static, dynamic, total_coll, stop_v, trig_v, total_red, ts])

        update_master_csv(self.team_name, total_lane, total_coll, total_red)

        self.lane.destroy()
        self.collision.destroy()

        print(f"\n=== Summary for {self.team_name} ===")
        print(f"Lane: {total_lane}  Collisions: {total_coll}  RedLight: {total_red}")

def main():
    team = input("Enter your team name: ").strip()
    if not team:
        print("❗ Team name required.")
        return

    client = carla.Client("localhost", 2000)
    client.set_timeout(10.0)
    TeamMonitor(client, team).run()
    input("Press Enter to exit...")

if __name__ == "__main__":
    main()
