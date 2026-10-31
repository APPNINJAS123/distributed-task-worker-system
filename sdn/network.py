SERVER = {"name": "srv", "ip": "10.0.0.1", "mac": "00:00:00:00:00:01"}

WORKERS = [
    {"name": "w1", "ip": "10.0.0.11", "mac": "00:00:00:00:00:11", "s4_port": 3, "load": 70},
    {"name": "w2", "ip": "10.0.0.12", "mac": "00:00:00:00:00:12", "s4_port": 4, "load": 10},
    {"name": "w3", "ip": "10.0.0.13", "mac": "00:00:00:00:00:13", "s4_port": 5, "load": 40},
]

HOSTS = [SERVER] + WORKERS

S1, S2, S3, S4 = 1, 2, 3, 4

S1_SERVER_PORT = 1
S1_UPLINK = {"s2": 2, "s3": 3}
CORE_TO_S1_PORT = 1
CORE_TO_S4_PORT = 2
S4_UPLINK = {"s2": 1, "s3": 2}
