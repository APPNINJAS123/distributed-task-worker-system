# Distributed Task Notification and Worker System

This is our computer networks college project. One server gives out small jobs (factorial, prime check, sorting and so on) to a few workers over TCP, and it always picks the least busy worker. An SDN controller (Ryu) also watches how busy each worker is and changes the switch rules so busy and idle workers use different network paths.

So the scheduler decides which worker gets a task, and the SDN controller decides how the traffic gets there.

It's all Python. The server, workers and client only use the standard library. Only the SDN part needs Mininet and Ryu.

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

The server and the workers are connected by two paths, one through s2 and one through s3. The controller sorts the workers by load and splits them between the two paths so one path doesn't get all the busy traffic. Say w1 is at 85%, w2 at 15% and w3 at 60%. Then w1 gets the s2 path to itself and w2 and w3 share s3. When the loads change, the controller rewrites the rules on s1 and s4 and the traffic moves.

## Folders

```
common/      config values and the message format (JSON, one message per line)
server/      task_server.py   - the server, queue, scheduler, results
worker/      worker.py        - the worker program
             tasks.py         - the task functions
client/      client.py        - command line tool to send tasks and check status
sdn/         topology.py      - Mininet network (4 switches, server, 3 workers)
             load_balancer.py - Ryu controller app
             path_planner.py  - decides which path each worker uses
             check_sdn.py     - runs the whole SDN demo and checks it (PASS/FAIL)
             network.py       - IPs, MACs and port numbers used by the files above
tests/       test_system.py   - automated tests
docker/      Dockerfile       - Linux image with Mininet, Open vSwitch and Ryu
```

## Who did what

We split the work into three parts, one each, and put them together at the end.

### Person 1: task server and scheduler (server/task_server.py, common/)

- TCP server on port 5050. Both workers and clients connect to it.
- Gives every task a unique id and keeps a queue of tasks waiting to run.
- Keeps a list of connected workers, with each one's load and how many tasks it's running.
- The scheduler picks the worker with the lowest load that still has a free slot.
- Saves the results when workers send them back.
- If a worker disconnects, or sends no heartbeat for 10 seconds, its unfinished tasks go back to the front of the queue and another worker picks them up.
- Every 2 seconds it writes the worker loads to /tmp/dtn_worker_loads.json. That's how the SDN part gets the loads.

### Person 2: workers and TCP communication (worker/worker.py, worker/tasks.py)

- Connects to the server and registers with a name (w1, w2 and so on).
- Gets tasks, runs each one in its own thread and sends the result back.
- Sends a heartbeat with its load every 2 seconds, and also right when a task starts or finishes.
- The --load option fakes some background load, so we can show the scheduler avoiding busy workers. With --vary, that load changes randomly every 15 seconds.
- If the server goes down, the worker retries every 3 seconds and registers again once it's back.
- Supported tasks: factorial, prime_check, sum_range, sort, matrix_multiply.

### Person 3: SDN and networking (sdn/)

- Mininet topology: the server is on s1, there are two parallel paths (s2 and s3), and the workers are on s4.
- Ryu controller using OpenFlow 1.3. It answers ARP requests itself, because the topology has a loop and normal flooding would go round forever. It also installs the IP forwarding rules.
- Every 3 seconds it reads the worker loads, works out a path for each worker and reroutes the ones whose path changed.
- It also reads port stats from s1 and prints how much traffic each path carries.
- path_planner.py runs on its own without Mininet, so you can show the routing decision on a normal laptop.

## Messages

Every message is one line of JSON sent over TCP. Some examples:

```
worker -> server   {"type": "REGISTER", "worker_id": "w2", "capacity": 2, "load": 10}
server -> worker   {"type": "TASK", "task_id": 21, "operation": "factorial", "input": 10}
worker -> server   {"type": "RESULT", "task_id": 21, "worker_id": "w2", "result": 3628800, "error": null}
worker -> server   {"type": "STATUS", "worker_id": "w2", "load": 35.0, "active": 1, "status": "available"}
client -> server   {"type": "SUBMIT", "operation": "prime_check", "input": 99991}
server -> client   {"type": "ACK", "task_id": 22}
```

The client can also send GET_RESULT (with a task_id) and STATUS.

## Running it on your laptop (without SDN)

You only need Python 3.8 or newer. Run every command from the project folder, each one in its own terminal.

Start the server:

```
python3 -m server.task_server
```

Start three workers with different fake loads:

```
python3 -m worker.worker --id worker1 --load 70
python3 -m worker.worker --id worker2 --load 10
python3 -m worker.worker --id worker3 --load 40
```

Check that they joined:

```
python3 -m client.client status
```

Send the demo batch of 10 tasks and wait for the results:

```
python3 -m client.client demo
```

Or send single tasks yourself:

```
python3 -m client.client submit factorial 10 --wait
python3 -m client.client submit sort "[5, 2, 9, 1]" --wait
python3 -m client.client submit matrix_multiply '{"a": [[1,2],[3,4]], "b": [[5,6],[7,8]]}' --wait
```

If you also want to see what the SDN controller would decide:

```
python3 -m sdn.path_planner
```

The server window prints a line for every task it hands out, like this one:

```
task 1 (factorial) -> worker2   loads: worker1=69% worker2=12% worker3=42%
```

so you can see it went to the least loaded worker.

## Running it with Mininet and Ryu

Mininet and Open vSwitch only run on Linux, so you need either Docker or a Linux VM.

### With Docker (Mac, Windows or Linux)

This is how we tested it. Have Docker Desktop running, then from the project folder:

```
docker build -t dtn-sdn -f docker/Dockerfile .
```

The first build takes a few minutes. Once it's done, this is the quickest way to check that the SDN part works:

```
docker run --rm -it --privileged -v "$PWD":/project dtn-sdn python3 -m sdn.check_sdn
```

It starts Ryu, builds the network, starts the server and workers inside it, sends tasks, changes the worker loads and checks that the controller really moved the traffic. Each step gets a PASS or FAIL line, and at the end you should see 11/11 checks passed. The Testing section below lists what it checks.

If you'd rather try things yourself, open a shell in the container:

```
docker run --rm -it --privileged --name dtn -v "$PWD":/project dtn-sdn bash
```

and run these inside it:

```
ryu-manager sdn/load_balancer.py > /tmp/dtn_ryu.log 2>&1 &
python3 -m sdn.topology
```

To watch the controller while you type commands in Mininet, open a second terminal on your computer and run:

```
docker exec -it dtn tail -f /tmp/dtn_ryu.log
```

Mininet creates network interfaces and switches, so the container needs --privileged. If Docker runs on a Linux machine, you might have to run sudo modprobe openvswitch on that machine first. Docker Desktop on a Mac already has it.

### With a Linux VM

Use Ubuntu 20.04, the same as the Dockerfile. One catch: on 20.04, apt install mininet only gives you the Python 2 version of the library, so you need the Python 3 one from pip as well:

```
sudo apt install mininet openvswitch-switch python3-pip
sudo pip3 install "setuptools<68" "eventlet==0.30.2" "ryu==4.34" https://github.com/mininet/mininet/archive/refs/tags/2.3.1b4.tar.gz
```

Then, from the project folder, run these two in separate terminals:

```
ryu-manager sdn/load_balancer.py
sudo python3 -m sdn.topology
```

You can run the self-check here too with sudo python3 -m sdn.check_sdn.

### Inside the mininet> prompt

sdn.topology starts the server on srv and the workers on w1, w2 and w3 for you (loads 70%, 10% and 40%). Then try:

```
pingall
srv python3 -m client.client status
srv python3 -m client.client demo
sh ovs-ofctl -O OpenFlow13 dump-flows s1
```

In the flow table, a line like nw_dst=10.0.0.11 actions=output:2 means traffic for w1 goes out port 2, towards s2. output:3 means towards s3. Run demo a few times and dump the flows again. The ports change as workers get busy.

The controller log looks something like this:

```
switch s1 connected
reroute w2 (load 11%): via s2 -> via s3
loads w1=67% w2=11% w3=38% | via s2 [w1] 1.0 KB/s | via s3 [w2,w3] 0.0 KB/s
reroute w3 (load 100%): via s3 -> via s2
```

The server and workers inside Mininet log to /tmp/dtn_server.log, /tmp/dtn_w1.log, /tmp/dtn_w2.log and /tmp/dtn_w3.log. To read one from the mininet prompt, use sh tail /tmp/dtn_server.log.

To start the server and workers yourself instead, run python3 -m sdn.topology --no-apps and use the same commands as the laptop section. Workers then need --server 10.0.0.1.

Type exit to stop everything. If something gets stuck, mn -c cleans it up. Inside Docker, Mininet prints "Error setting resource limits". You can ignore it.

## Testing

### Automated tests

```
python3 -m unittest tests.test_system -v
```

These start a real server and workers on port 5099 and check that:

- each task function gives the right answer
- the least loaded worker gets the task
- a batch of tasks all finish and are spread over more than one worker
- if a worker is killed in the middle of a task, the task goes back in the queue and another worker finishes it
- unknown operations are rejected
- the path planner gives the busiest worker its own path

### SDN check

python3 -m sdn.check_sdn (inside Docker, or with sudo on Linux) tests the whole SDN part on its own:

1. all 4 switches connect to the Ryu controller
2. pingall works with 0% loss, which means the controller's ARP replies and flow rules are correct
3. the server and 3 workers start inside Mininet and register over TCP
4. the controller puts w1 (70%) alone on the s2 path and w2 and w3 on s3 (read straight from the flow table on s1)
5. 20 tasks go through the network and all of them finish
6. the packet counters on s2 and s3 both go up, so both paths really carried traffic
7. w1 is restarted at 5% load and w2 at 95%, and it waits for the controller to swap them so w2 is alone on s2
8. the reroute shows up in the controller log
9. a new task still works after the reroute
10. pings to w2 now go through s2 and pings to w1 go through s3 (checked with the switch counters)

Some steps check more than one thing, so there are 11 checks in total. If all 11 pass, the SDN part works.

### Things to try by hand for the demo

Load based scheduling: start the workers with different --load values and run client demo. The least loaded worker gets the first tasks, and once it fills up the server moves on to the next one.

Worker failure: start a slow worker so it holds on to its tasks for a while:

```
python3 -m worker.worker --id slowpoke --load 0 --delay 8
```

Run client demo, then press Ctrl+C on slowpoke while it's working. The server prints "worker slowpoke disconnected" and "put tasks [...] back in the queue", and the demo still finishes all 10 tasks. In Mininet you can do the same with w2 pkill -f "id w2".

Worker reconnecting: stop the server while the workers are running. They keep printing "trying again in 3 seconds", and when you start the server again they register on their own.

SDN rerouting: in Mininet, run srv python3 -m client.client demo --repeat 3. As the workers fill up their loads go up, and the Ryu terminal shows reroute lines. You can also restart a worker with a different load:

```
w1 pkill -f "id w1"
w1 python3 -m worker.worker --id w1 --server 10.0.0.1 --load 5 > /tmp/dtn_w1.log 2>&1 &
```

Or start the workers with --vary so their load changes on its own every 15 seconds.

## What we kept simple on purpose

- results are only kept in the server's memory, there's no database
- no login or security on the sockets
- the topology and port numbers are fixed in sdn/network.py
- the controller balances by worker load only. It shows how much traffic each path carries but doesn't use that to decide.
- worker load is simulated (background load plus a share for each running task), not real CPU usage

## Common problems

- "Address already in use": an old server is still running. Stop it, or pass --port 5051 to everything.
- On a Mac, AirPlay uses port 5000. That's why we use 5050.
- "ModuleNotFoundError: No module named 'mininet'": you have the Python 2 Mininet from apt. Install the pip one (see the Linux VM section) or use Docker.
- Workers in Mininet say the server isn't reachable: make sure the controller is running before you start the topology. Without it the switches have no rules and nothing can talk.
- pingall fails: same cause. The controller isn't running or has crashed, so check its terminal.
