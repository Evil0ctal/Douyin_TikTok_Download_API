"""Test that concurrent setup requests cannot create multiple admin accounts.

Verifies the fix for the non-atomic token consumption race condition
(CVSS 9.1) where redis.get() + redis.delete() allowed two concurrent
requests to both see the token and create separate ADMIN accounts.
"""
import asyncio
import pytest


@pytest.mark.asyncio
async def test_concurrent_setup_creates_only_one_admin(client, redis_client):
    """Two concurrent POST /api/setup/init with the same token should
    create exactly one admin account, not two."""
    import uuid

    # Set up a fresh setup token
    token = "test-token-" + str(uuid.uuid4())
    await redis_client.set("setup:token", token, ex=3600)
    await redis_client.delete("setup:attempts")
    await redis_client.delete("setup:claim")

    # Fire two concurrent requests with the same token
    payload_a = {"token": token, "username": "admin_a", "password": "TestPass123!"}
    payload_b = {"token": token, "username": "admin_b", "password": "TestPass123!"}

    resp_a, resp_b = await asyncio.gather(
        client.post("/api/setup/init", json=payload_a),
        client.post("/api/setup/init", json=payload_b),
    )

    # Exactly one should succeed with 201
    statuses = sorted([resp_a.status_code, resp_b.status_code])
    assert statuses == [201, 409], (
        f"Expected one 201 and one 409, got {statuses}. "
        f"Race condition not fixed — both requests may have created admin accounts."
    )

    # The successful request should have created an admin
    success_resp = resp_a if resp_a.status_code == 201 else resp_b
    assert success_resp.json()["data"]["user"]["role"] == "admin"


@pytest.mark.asyncio
async def test_serial_setup_second_fails(client, redis_client):
    """Serialized setup requests: second request must get 409."""
    import uuid

    token = "test-token-" + str(uuid.uuid4())
    await redis_client.set("setup:token", token, ex=3600)
    await redis_client.delete("setup:attempts")
    await redis_client.delete("setup:claim")

    resp1 = await client.post(
        "/api/setup/init",
        json={"token": token, "username": "first_admin", "password": "TestPass123!"},
    )
    assert resp1.status_code == 201

    resp2 = await client.post(
        "/api/setup/init",
        json={"token": token, "username": "second_admin", "password": "TestPass123!"},
    )
    assert resp2.status_code == 409
