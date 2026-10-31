# Distributed Task Notification and Worker System

Our college project for computer networks. One server hands out small computing jobs (factorial, prime check, sorting, etc.) to a few worker machines over TCP. It always picks the least busy worker. On top of that, an SDN controller (Ryu) watches how loaded each worker is and changes the switch rules so the busy workers' traffic and the idle workers' traffic go over different network paths.

Basically:

- the **scheduler** decides *which* worker gets a task
- the **SDN controller** decides *how* the traffic gets to that worker

Everything is plain Python. The server, workers and client only use the standard library. Only the SDN part needs Mininet and Ryu.

## How it looks

```
   client
     |
     v
  +-------------+                       +------------------+
  | task server |  writes worker loads  |  Ryu controller  |
  |  queue      | --------------------> |  (load_balancer) |
  |  scheduler  |  /tmp/dtn_worker_     +--------+---------+
  +------+------+       loads.json               | OpenFlow rules
         |                                       v
        s1 --------- s2 ---------+
         \                       s4 ---- w1, w2, w3
          ---------- s3 ---------+
```

There are two paths between the server and the workers (through s2 or through s3). The controller sorts workers by load and splits them across the two paths so neither path ends up carrying all the busy traffic. For example with w1=85%, w2=15%, w3=60%, w1 gets the s2 path to itself and w2 + w3 share s3. When loads change, it rewrites the flow rules on s1 and s4 and the traffic moves over.

## Folder layout

```
common/      config values + the message format (JSON, one message per line)
server/      task_server.py   - the server, queue, scheduler, results
worker/      worker.py        - the worker program
             tasks.py         - the actual task functions
client/      client.py        - command line tool to send tasks / check status
sdn/         topology.py      - Mininet network (4 switches, server, 3 workers)
             load_balancer.py - Ryu controller app
             path_planner.py  - the "which path for which worker" logic
             check_sdn.py     - runs the whole SDN demo and checks it, PASS/FAIL
             network.py       - IPs, MACs and port numbers shared by the two above
tests/       test_system.py   - automated tests
docker/      Dockerfile       - Linux image with Mininet, Open vSwitch and Ryu
```

## Who did what

We split it into three parts so each of us owned one piece. Then we put them together at the end.

**Person 1 - task server and scheduler** (`server/task_server.py`, `common/`)

- TCP server on port 5050 that accepts both workers and clients
- gives every task a unique id and keeps a queue of pending tasks
- keeps a list of connected workers with their load and how many tasks they're running
- scheduler: picks the worker with the lowest load that still has a free slot
- stores results when workers send them back
- if a worker disconnects or stops sending heartbeats for 10 seconds, its unfinished tasks go back to the front of the queue so someone else does them
- every 2 seconds writes the worker loads to `/tmp/dtn_worker_loads.json` (this is how the SDN part finds out the loads)

**Person 2 - workers and TCP communication** (`worker/worker.py`, `worker/tasks.py`)

- connects to the server, registers with a name (w1, w2...)
- receives tasks, runs them in a thread, sends the result back
- sends a heartbeat with its load every 2 seconds, and also right when a task starts or finishes
- `--load` pretends the machine already has some background load, so we can show the scheduler avoiding busy workers. `--vary` makes that load jump around every 15s
- if the server goes away, the worker keeps retrying every 3 seconds and re-registers when it's back
- tasks supported: `factorial`, `prime_check`, `sum_range`, `sort`, `matrix_multiply`

**Person 3 - SDN and networking** (`sdn/`)

- Mininet topology: server on s1, two parallel paths (s2 and s3), workers on s4
- Ryu controller using OpenFlow 1.3. It answers ARP itself (the topology has a loop, so plain flooding would loop forever) and installs IP forwarding rules
- every 3 seconds it reads the worker loads, works out the path for each worker and reroutes the ones whose path changed
- also polls port stats on s1 and prints how much traffic each path is carrying
- `path_planner.py` can run on its own without Mininet, handy for showing the routing decision on a normal laptop

## Message format

Every message is one line of JSON over TCP. A few examples:

```
worker -> server   {"type": "REGISTER", "worker_id": "w2", "capacity": 2, "load": 10}
server -> worker   {"type": "TASK", "task_id": 21, "operation": "factorial", "input": 10}
worker -> server   {"type": "RESULT", "task_id": 21, "worker_id": "w2", "result": 3628800, "error": null}
worker -> server   {"type": "STATUS", "worker_id": "w2", "load": 35.0, "active": 1, "status": "available"}
client -> server   {"type": "SUBMIT", "operation": "prime_check", "input": 99991}
server -> client   {"type": "ACK", "task_id": 22}
```

The client can also send `GET_RESULT` (with a task_id) and `STATUS`.

## Running it on your own laptop (no SDN)

You need Python 3.8 or newer, nothing else. Run every command from the project folder, each in its own terminal.

1. Start the server:

   ```
   python3 -m server.task_server
   ```

2. Start three workers with different fake loads:

   ```
   python3 -m worker.worker --id worker1 --load 70
   python3 -m worker.worker --id worker2 --load 10
   python3 -m worker.worker --id worker3 --load 40
   ```

3. Check that they joined:

   ```
   python3 -m client.client status
   ```

4. Send the demo batch of 10 tasks and wait for the results:

   ```
   python3 -m client.client demo
   ```

   Or send one task yourself:

   ```
   python3 -m client.client submit factorial 10 --wait
   python3 -m client.client submit sort "[5, 2, 9, 1]" --wait
   python3 -m client.client submit matrix_multiply '{"a": [[1,2],[3,4]], "b": [[5,6],[7,8]]}' --wait
   ```

5. Optional, see what the SDN controller would decide:

   ```
   python3 -m sdn.path_planner
   ```

In the server window you'll see lines like

```
task 1 (factorial) -> worker2   loads: worker1=69% worker2=12% worker3=42%
```

so you can see it picked the least loaded one.

## Running the full thing with Mininet + Ryu

Mininet and Open vSwitch only run on Linux. There are two ways to get that.

### Option A: Docker (works on Mac, Windows and Linux)

This is how we tested it. You just need Docker Desktop running. From the project folder:

```
docker build -t dtn-sdn -f docker/Dockerfile .
```

The first build takes a few minutes. After that, the quickest way to see that the SDN part works:

```
docker run --rm -it --privileged -v "$PWD":/project dtn-sdn python3 -m sdn.check_sdn
```

That starts Ryu, builds the network, runs the server and workers inside it, sends tasks, changes the worker loads and checks that the controller actually moved the traffic. It prints a PASS/FAIL line for each step and should end with `11/11 checks passed`. More on what it checks in the Testing section.

To play with it yourself instead, open a shell in the container:

```
docker run --rm -it --privileged --name dtn -v "$PWD":/project dtn-sdn bash
```

and inside it:

```
ryu-manager sdn/load_balancer.py > /tmp/dtn_ryu.log 2>&1 &
python3 -m sdn.topology
```

To watch the controller while you type commands in Mininet, open a second terminal on your Mac and run:

```
docker exec -it dtn tail -f /tmp/dtn_ryu.log
```

(`--privileged` is needed because Mininet creates network interfaces and switches. If your Docker host is a Linux machine, you may need `sudo modprobe openvswitch` on the host first. Docker Desktop on Mac already has it.)

### Option B: a Linux VM

Use Ubuntu 20.04, with the same versions the Dockerfile uses. One catch: on 20.04 `apt install mininet` only installs the Python 2 version of the library, so you also need the Python 3 one from pip:

```
sudo apt install mininet openvswitch-switch python3-pip
sudo pip3 install "setuptools<68" "eventlet==0.30.2" "ryu==4.34" https://github.com/mininet/mininet/archive/refs/tags/2.3.1b4.tar.gz
```

Then, from the project folder, use `ryu-manager sdn/load_balancer.py` in one terminal and `sudo python3 -m sdn.topology` in another. You can also run the self-check with `sudo python3 -m sdn.check_sdn`.

### What to do in the mininet> prompt

`sdn.topology` already starts the server on `srv` and the three workers on `w1`, `w2`, `w3` (loads 70%, 10%, 40%). Then:

```
pingall
srv python3 -m client.client status
srv python3 -m client.client demo
sh ovs-ofctl -O OpenFlow13 dump-flows s1
```

In the flow table, `nw_dst=10.0.0.11 actions=output:2` means traffic to w1 goes out port 2 (towards s2), and `output:3` means towards s3. Run `demo` a few times and dump the flows again. As workers get busy the ports change.

The controller log looks like this:

```
switch s1 connected
reroute w2 (load 11%): via s2 -> via s3
loads w1=67% w2=11% w3=38% | via s2 [w1] 1.0 KB/s | via s3 [w2,w3] 0.0 KB/s
reroute w3 (load 100%): via s3 -> via s2
```

Logs for the apps inside Mininet go to `/tmp/dtn_server.log`, `/tmp/dtn_w1.log`, `/tmp/dtn_w2.log`, `/tmp/dtn_w3.log` (`sh tail /tmp/dtn_server.log` from the mininet prompt).

If you'd rather start the server and workers yourself, use `python3 -m sdn.topology --no-apps` and run the same commands as in the laptop section. Workers need `--server 10.0.0.1`.

Type `exit` to stop everything. If something got stuck, `mn -c` cleans up. Mininet prints "Error setting resource limits" inside Docker, which is harmless.

## Testing

### Automated tests

```
python3 -m unittest tests.test_system -v
```

This starts a real server and workers on port 5099 and checks:

- each task function gives the right answer
- the least loaded worker gets the task
- a batch of tasks all finish and get spread over more than one worker
- if a worker is killed in the middle of a task, the task gets requeued and another worker finishes it
- unknown operations are rejected
- the path planner gives the busiest worker its own path

### SDN check

`python3 -m sdn.check_sdn` (inside Docker or with sudo on Linux) goes through the whole SDN part by itself:

1. all 4 switches connect to the Ryu controller
2. pingall works (0% loss), so the controller's ARP replies and flow rules are right
3. the server and 3 workers start inside Mininet and register over TCP
4. the controller puts w1 (70%) alone on the s2 path and w2 + w3 on s3, which it reads straight from s1's flow table
5. 20 tasks go through the network and all finish
6. packet counters on s2 and s3 both went up, so both paths were really used
7. w1 is restarted at 5% load and w2 at 95%, then it waits for the controller to swap them (w2 alone on s2 now)
8. the reroute shows up in the controller log
9. a new task still works after the reroute
10. pings to w2 now go through s2 and pings to w1 through s3, checked with the switch counters

If all 11 checks pass, the SDN part is doing what it's supposed to.

### Things to try by hand for the demo

**Load based scheduling.** Run the workers with different `--load` values and run `client demo`. The low load worker gets the first tasks, and as it fills up the server moves on to the next one.

**Worker failure.** Start a worker with a slow delay so it holds tasks for a while:

```
python3 -m worker.worker --id slowpoke --load 0 --delay 8
```

Run `client demo`, then kill slowpoke (Ctrl+C) while it's working. The server prints `worker slowpoke disconnected` and `put tasks [...] back in the queue`, and the demo still ends with all 10 tasks done. In Mininet you can do the same with `w2 pkill -f "id w2"`.

**Worker reconnects.** Stop the server while workers are running. They keep printing "trying again in 3 seconds". Start the server again and they register by themselves.

**SDN rerouting.** In Mininet, run `srv python3 -m client.client demo --repeat 3`. As workers fill up their load goes up and you'll see `reroute ...` lines in the Ryu terminal. You can also restart a worker with a different load:

```
w1 pkill -f "id w1"
w1 python3 -m worker.worker --id w1 --server 10.0.0.1 --load 5 > /tmp/dtn_w1.log 2>&1 &
```

Or start workers with `--vary` so their load changes on its own every 15 seconds.

## Things we kept simple on purpose

- results only live in the server's memory, no database
- no login or security on the sockets
- the network topology and the port numbers are fixed in `sdn/network.py`
- the controller only balances by worker load, not by measuring congestion (it shows the traffic numbers but doesn't use them to decide)
- worker "load" is simulated (background load + share of running tasks), not real CPU usage

## Common problems

- `Address already in use`: an old server is still running. Kill it, or use `--port 5051` on everything.
- On a Mac, port 5000 is taken by AirPlay, that's why we use 5050.
- `ModuleNotFoundError: No module named 'mininet'`: you have the Python 2 Mininet from apt. Install the pip one (see Option B) or use Docker.
- Workers in Mininet say server not reachable: check the controller is running before you start the topology. Without it the switches have no rules and nothing can talk.
- `pingall` fails: same thing, controller isn't running or crashed. Look at terminal 1.
