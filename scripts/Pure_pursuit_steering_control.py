import carla
import random
import math
import time
import numpy as np
import heapq
import itertools

# ======== A* PATH PLANNER =========
def a_star(start_wp, goal_wp, map):
    counter = itertools.count()
    open_set = [(0, next(counter), start_wp)]
    came_from = {}
    g_score = {start_wp: 0}

    while open_set:
        _, _, current = heapq.heappop(open_set)
        if current.transform.location.distance(goal_wp.transform.location) < 2.0:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            return list(reversed(path))

        for neighbor in current.next(2.0):
            tentative_g = g_score[current] + current.transform.location.distance(neighbor.transform.location)
            if neighbor not in g_score or tentative_g < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                f_score = tentative_g + neighbor.transform.location.distance(goal_wp.transform.location)
                heapq.heappush(open_set, (f_score, next(counter), neighbor))

    return []

# ======== SMOOTH PATH =========
def smooth_path(path, step=1.0):
    smoothed = []
    for i in range(len(path) - 1):
        p1, p2 = path[i].transform.location, path[i + 1].transform.location
        dist = p1.distance(p2)
        for j in range(int(dist / step)):
            t = j / int(dist / step)
            smoothed.append(carla.Location(
                x=p1.x + t * (p2.x - p1.x),
                y=p1.y + t * (p2.y - p1.y),
                z=p1.z + t * (p2.z - p1.z)))
    smoothed.append(path[-1].transform.location)
    return smoothed

# ======== PURE PURSUIT =========
def pure_pursuit(vehicle, target):
    loc = vehicle.get_location()
    yaw = math.radians(vehicle.get_transform().rotation.yaw)

    dx = target.x - loc.x
    dy = target.y - loc.y

    tx = math.cos(yaw) * dx + math.sin(yaw) * dy
    ty = -math.sin(yaw) * dx + math.cos(yaw) * dy

    if tx <= 0.1:
        return 0.0

    curvature = 2 * ty / (tx ** 2 + ty ** 2)
    steer = np.clip(curvature * 0.9, -1.0, 1.0)
    return steer

# ======== MAIN =========
def main():
    client = carla.Client("localhost", 2000)
    client.set_timeout(10.0)
    world = client.get_world()
    map = world.get_map()
    blueprint_library = world.get_blueprint_library()

    spawn_points = map.get_spawn_points()
    start_transform = random.choice(spawn_points) # we can provide custom starting spawn point
    end_transform = random.choice(spawn_points) # we can provide custom destination spawn point

    print("Start Location:", start_transform.location)
    print("End Location:", end_transform.location)

    vehicle_bp = blueprint_library.filter("vehicle.tesla.model3")[0]
    vehicle = world.try_spawn_actor(vehicle_bp, start_transform)

    if not vehicle:
        print("Vehicle spawn failed.")
        return

    # Setup chase camera
    spectator = world.get_spectator()

    start_wp = map.get_waypoint(start_transform.location, project_to_road=True)
    end_wp = map.get_waypoint(end_transform.location, project_to_road=True)

    print("Computing A* path...")
    path = a_star(start_wp, end_wp, map)
    if not path:
        print("Failed to find path.")
        return
    
    print("Smoothing path...")
    path = smooth_path(path, step=1.5)

    print(f"Generated {len(path)} path points.")
    index = 0
    try:
        while True:
            world.tick()

            # Update chase camera
            transform = vehicle.get_transform()
            loc = transform.location
            yaw_rad = math.radians(transform.rotation.yaw)
            offset = carla.Location(
                x=loc.x - math.cos(yaw_rad) * 10.0,
                y=loc.y - math.sin(yaw_rad) * 10.0,
                z=loc.z + 6.0)
            spectator.set_transform(carla.Transform(offset, carla.Rotation(pitch=-20, yaw=transform.rotation.yaw)))

            if index >= len(path):
                print("Destination reached.")
                vehicle.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
                break

            target = path[min(index + 5, len(path) - 1)]
            steer = pure_pursuit(vehicle, target)

            control = carla.VehicleControl(
                throttle=0.3,
                steer=steer,
                brake=0.0
            )

            vehicle.apply_control(control)

            print(f"Throttle: {control.throttle:.2f} | Brake: {control.brake:.2f} | Steer: {control.steer:.3f}")

            if loc.distance(target) < 2.0:
                index += 1

            time.sleep(1.0 / 20.0)

    finally:
        print("Cleaning up...")
        vehicle.destroy()

if __name__ == '__main__':
    main()
