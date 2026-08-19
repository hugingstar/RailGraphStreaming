# -*- coding: utf-8 -*-
"""Thin conveniences over aiokafka: JSON codecs and pre-wired client factories."""
from __future__ import annotations

import logging

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewPartitions, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from . import config

log = logging.getLogger(__name__)

# rail.plans is a state topic: keyed by train, compacted, replayed on restart.
TOPIC_SPEC: dict[str, dict] = {
    config.TOPIC_PLANS: {"partitions": 6, "config": {"cleanup.policy": "compact",
                                                     "segment.ms": "60000",
                                                     "min.cleanable.dirty.ratio": "0.1"}},
    config.TOPIC_OBSERVATIONS: {"partitions": 6, "config": {"retention.ms": "86400000"}},
    config.TOPIC_POSITIONS: {"partitions": 6, "config": {"retention.ms": "600000"}},
    config.TOPIC_ALERTS: {"partitions": 3, "config": {"retention.ms": "86400000"}},
}


def dumps(obj) -> bytes:
    # OPT_SERIALIZE_NUMPY keeps a stray np.float64 from taking the pipeline down.
    return orjson.dumps(obj, option=orjson.OPT_SERIALIZE_NUMPY)


def loads(raw: bytes):
    return orjson.loads(raw)


def key(k: str) -> bytes:
    return k.encode("utf-8")


def producer(**kw) -> AIOKafkaProducer:
    return AIOKafkaProducer(
        bootstrap_servers=config.KAFKA_BOOTSTRAP,
        value_serializer=dumps,
        key_serializer=lambda k: k if k is None else key(k),
        linger_ms=kw.pop("linger_ms", 20),
        compression_type=kw.pop("compression_type", "gzip"),
        acks=kw.pop("acks", 1),
        **kw,
    )


def consumer(*topics: str, group_id: str, offset: str = "earliest",
             auto_commit: bool = False, **kw) -> AIOKafkaConsumer:
    return AIOKafkaConsumer(
        *topics,
        bootstrap_servers=config.KAFKA_BOOTSTRAP,
        group_id=group_id,
        value_deserializer=loads,
        key_deserializer=lambda k: k if k is None else k.decode("utf-8"),
        auto_offset_reset=offset,
        enable_auto_commit=auto_commit,
        **kw,
    )


async def ensure_topics(replication: int = 1) -> None:
    """Create the topics, then verify they really look the way we asked.

    A broker with auto-creation enabled will happily invent a one-partition
    topic the moment anything touches it, which silently caps consumer
    parallelism.  So we create, re-describe, and grow anything that came out
    too small rather than trusting the create call.
    """
    admin = AIOKafkaAdminClient(bootstrap_servers=config.KAFKA_BOOTSTRAP)
    await admin.start()
    try:
        wanted = [
            NewTopic(name=name, num_partitions=spec["partitions"],
                     replication_factor=replication, topic_configs=spec["config"])
            for name, spec in TOPIC_SPEC.items()
        ]
        try:
            await admin.create_topics(wanted)
        except TopicAlreadyExistsError:
            pass

        actual = {t["topic"]: len(t["partitions"])
                  for t in await admin.describe_topics(list(TOPIC_SPEC))
                  if not t.get("error_code")}
        undersized = {
            name: NewPartitions(spec["partitions"])
            for name, spec in TOPIC_SPEC.items()
            if 0 < actual.get(name, 0) < spec["partitions"]
        }
        if undersized:
            log.warning("growing under-provisioned topics: %s", ", ".join(undersized))
            await admin.create_partitions(undersized)
            actual = {t["topic"]: len(t["partitions"])
                      for t in await admin.describe_topics(list(TOPIC_SPEC))
                      if not t.get("error_code")}
        log.info("topics ready: %s",
                 ", ".join(f"{n}[{actual.get(n, 0)}p]" for n in TOPIC_SPEC))
    finally:
        await admin.close()


async def drain_backlog(cons: AIOKafkaConsumer, handler, idle_polls: int = 2,
                        timeout_ms: int = 700) -> int:
    """Replay whatever is already in the log until the consumer goes quiet.

    Used at startup to rebuild in-memory state from the compacted plan topic
    before the service starts doing real work.
    """
    n, idle = 0, 0
    while idle < idle_polls:
        batches = await cons.getmany(timeout_ms=timeout_ms)
        if not batches:
            idle += 1
            continue
        idle = 0
        for tp, msgs in batches.items():
            for msg in msgs:
                handler(tp.topic, msg.key, msg.value)
                n += 1
    return n
