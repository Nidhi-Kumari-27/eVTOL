import carla
import time
import csv
import os
import math
from collections import defaultdict
from datetime import datetime

ZONE_RADIUS = 2.0
OUTPUT_DIR = '.'


def euclidean_distance(loc1, loc2):
    return math.sqrt((loc1.x - loc2.x) ** 2 + (loc1.y - loc2.y) ** 2)


class CollisionMonitor:
    def __init__(self, vehicle, world):
        self.vehicle = vehicle
        self.world = world
        self.sensor = None
        self.zones = []
        self.static_count = 0
        self.dynamic_count = 0

    def attach_sensor(self):
        bp = self.world.get_blueprint_library().find('sensor.other.collision')
        self.sensor = self.world.spawn_actor(bp, carla.Transform(), attach_to=self.vehicle)
        self.sensor.listen(self._on_collision)
        print("📡 Collision sensor attached.")

    def _on_collision(self, event):
        loc = self.vehicle.get_location()
        self._cleanup_zones(loc)

        if any(euclidean_distance(loc, zone) < ZONE_RADIUS for zone in self.zones):
            return

        other = event.other_actor
        if 'vehicle' in other.type_id:
            self.dynamic_count += 1
            print(f"💥 Dynamic collision with {other.type_id}")
        else:
            self.static_count += 1
            print(f"💥 Static collision with {other.type_id}")

        self.zones.append(loc)

    def _cleanup_zones(self, current_loc):
        self.zones = [z for z in self.zones if euclidean_distance(current_loc, z) < ZONE_RADIUS]

    def destroy(self):
        if self.sensor:
            self.sensor.stop()
            self.sensor.destroy()

    def total_collisions(self):
        return self.static_count + self.dynamic_count


class LaneMonitor:
    def __init__(self, vehicle, world):
        self.vehicle = vehicle
        self.world = world
        self.sensor = None
        self.last_turn_time = 0
        self.violations = defaultdict(int)

    def attach_sensor(self):
        bp = self.world.get_blueprint_library().find('sensor.other.lane_invasion')
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
        return math.sqrt(v.x ** 2 + v.y ** 2 + v.z ** 2)

    def get_stop_data(self, tl):
        waypoints = tl.get_stop_waypoints()
        if waypoints:
            wp = waypoints[0]
            return wp.transform.location, wp.transform.rotation.get_forward_vector()
        return None, None

    def is_inside_trigger_box(self, tl):
        trigger = tl.trigger_volume
        world_loc = tl.get_transform().transform(trigger.location)
        veh_loc = self.vehicle.get_location()
        dx = abs(world_loc.x - veh_loc.x)
        dy = abs(world_loc.y - veh_loc.y)
        dz = abs(world_loc.z - veh_loc.z)
        return dx <= trigger.extent.x and dy <= trigger.extent.y and dz <= trigger.extent.z

    def tick(self):
        tl = self.vehicle.get_traffic_light()
        if tl is None:
            self.violation_logged = False
            return

        if tl.get_state() != carla.TrafficLightState.Red:
            self.violation_logged = False
            return

        speed = self.get_speed()
        veh_loc = self.vehicle.get_location()
        stop_loc, stop_fwd = self.get_stop_data(tl)

        violation_type = None
        distance_past = None

        if stop_loc:
            rel = veh_loc - stop_loc
            dot = rel.x * stop_fwd.x + rel.y * stop_fwd.y + rel.z * stop_fwd.z
            if dot > 0 and speed > 1.0:
                violation_type = "StopWaypointPassed"
                distance_past = dot
        elif self.is_inside_trigger_box(tl) and speed > 1.0:
            violation_type = "TriggerVolume"
            distance_past = veh_loc.distance(tl.get_transform().location)

        if violation_type and not self.violation_logged:
            self.violations[violation_type] += 1
            print(f"🚦 RED LIGHT VIOLATION: {violation_type} | Distance Past: {distance_past:.2f}")
            self.violation_logged = True
        elif not violation_type:
            self.violation_logged = False


class TeamMonitor:
    def __init__(self, client, team_name):
        self.client = client
        self.world = client.get_world()
        self.team_name = team_name.lower().replace(" ", "_")
        self.vehicle = None

        self.lane_monitor = None
        self.collision_monitor = None
        self.redlight_monitor = None

        self.output_csv = self.get_unique_filename()
        self.setup_csv()

    def get_unique_filename(self):
        return os.path.join(OUTPUT_DIR, f"{self.team_name}_summary.csv")

    def setup_csv(self):
        if not os.path.exists(self.output_csv):
            with open(self.output_csv, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "illegal_double_solid_cross",
                    "illegal_solid_cross",
                    "unjustified_dashed_cross",
                    "total_lane_violations",
                    "static_collisions",
                    "dynamic_collisions",
                    "total_collisions",
                    "redlight_StopWaypointPassed",
                    "redlight_TriggerVolume",
                    "total_redlight_violations"
                ])

    def find_vehicle(self):
        existing_ids = {v.id for v in self.world.get_actors().filter('vehicle.*')}
        print("⏳ Waiting for ego vehicle to spawn...")
        while not self.vehicle:
            for v in self.world.get_actors().filter('vehicle.*'):
                if v.id not in existing_ids:
                    self.vehicle = v
                    print(f"🚗 Ego vehicle detected: {v.type_id}, ID {v.id}")
                    return
            time.sleep(0.5)

    def run(self):
        self.find_vehicle()

        self.lane_monitor = LaneMonitor(self.vehicle, self.world)
        self.collision_monitor = CollisionMonitor(self.vehicle, self.world)
        self.redlight_monitor = RedLightMonitor(self.vehicle)

        self.lane_monitor.attach_sensor()
        self.collision_monitor.attach_sensor()

        try:
            while True:
                self.lane_monitor.update_turn_status()
                self.redlight_monitor.tick()
                self.collision_monitor._cleanup_zones(self.vehicle.get_transform().location)
                active_ids = {v.id for v in self.world.get_actors().filter('vehicle.*')}
                if self.vehicle.id not in active_ids:
                    print(" Vehicle removed. Exiting.")
                    break
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("⛔ Interrupted manually.")
        finally:
            self.cleanup()

    def cleanup(self):
        # LANE
        double_solid = self.lane_monitor.violations["illegal_double_solid_cross"]
        solid = self.lane_monitor.violations["illegal_solid_cross"]
        dashed = self.lane_monitor.violations["unjustified_dashed_cross"]
        total_lane = double_solid + solid + dashed

        # COLLISIONS
        static = self.collision_monitor.static_count
        dynamic = self.collision_monitor.dynamic_count
        total_coll = static + dynamic

        # RED LIGHT
        stop_violation = self.redlight_monitor.violations["StopWaypointPassed"]
        trigger_violation = self.redlight_monitor.violations["TriggerVolume"]
        total_red = stop_violation + trigger_violation

        print("\n=== Summary ===")
        print(f"Double Solid Crosses: {double_solid}")
        print(f"Solid Crosses: {solid}")
        print(f"Dashed Crosses: {dashed}")
        print(f"Total Lane Violations: {total_lane}")
        print(f"Static Collisions: {static}")
        print(f"Dynamic Collisions: {dynamic}")
        print(f"Total Collisions: {total_coll}")
        print(f"Red Light Violations (Stop Line): {stop_violation}")
        print(f"Red Light Violations (Trigger Zone): {trigger_violation}")
        print(f"Total Red Light Violations: {total_red}")

        with open(self.output_csv, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                double_solid, solid, dashed, total_lane,
                static, dynamic, total_coll,
                stop_violation, trigger_violation, total_red
            ])

        self.lane_monitor.destroy()
        self.collision_monitor.destroy()


def main():
    team_name = input("Enter your team name: ").strip()
    if not team_name:
        print("❗ Team name is required.")
        return

    client = carla.Client("localhost", 2000)
    client.set_timeout(10.0)

    monitor = TeamMonitor(client, team_name)
    monitor.run()


if __name__ == "__main__":
    main()
