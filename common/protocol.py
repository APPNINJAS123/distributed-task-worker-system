import json

ENCODING = "utf-8"


def send_msg(sock, msg):
    sock.sendall((json.dumps(msg) + "\n").encode(ENCODING))


def read_messages(sock):
    reader = sock.makefile("r", encoding=ENCODING)
    for line in reader:
        line = line.strip()
        if line:
            yield json.loads(line)


def short(value, limit=60):
    text = value if isinstance(value, str) else json.dumps(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text
