"""Basic connection example.
"""

import redis

r = redis.Redis(
    host='hypercozy-sensate-ambery-30801.db.redis.io',
    port=11745,
    decode_responses=True,
    username="default",
    password="sR374S3og4fZ9hn9cHWVBXmOOjVVnxcQ",
)

success = r.set('foo', 'bar')
# True

result = r.get('foo')
print(result)
# >>> bar

