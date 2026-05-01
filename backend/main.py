from datetime import datetime
import os
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

app = FastAPI(title="I M Your Solution API", version="1.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URL = os.getenv("MONGO_URL", "").strip()
DB_NAME = os.getenv("DB_NAME", "imyoursolution").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234").strip()

mongo_client: Optional[AsyncIOMotorClient] = None
db = None

# Fallback memory store: keeps Render service running even if MongoDB is not added yet.
# Add MONGO_URL in Render for permanent saved data.
MEMORY_CLIENTS: List[Dict[str, Any]] = []
MEMORY_COUNTER = 0

if MONGO_URL:
    mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = mongo_client[DB_NAME]


class LoginRequest(BaseModel):
    username: str
    password: str


class ClientIn(BaseModel):
    name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=1)
    amount: float = 0
    contract: str = ""
    dueDate: str = ""
    status: str = "Pending"
    recurring: str = ""
    notes: str = ""
    documentName: str = ""
    documentData: str = ""


class ClientStatusUpdate(BaseModel):
    status: str = "Pending"


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def serialize_client(doc: Dict[str, Any]) -> Dict[str, Any]:
    raw_id = doc.get("_id") or doc.get("id") or ""
    return {
        "id": str(raw_id),
        "invoiceNo": doc.get("invoiceNo", ""),
        "name": doc.get("name", ""),
        "phone": doc.get("phone", ""),
        "amount": float(doc.get("amount", 0) or 0),
        "contract": doc.get("contract", ""),
        "dueDate": doc.get("dueDate", ""),
        "status": doc.get("status", "Pending"),
        "recurring": doc.get("recurring", ""),
        "notes": doc.get("notes", ""),
        "documentName": doc.get("documentName", ""),
        "documentData": doc.get("documentData", ""),
        "createdAt": doc.get("createdAt", ""),
        "updatedAt": doc.get("updatedAt", ""),
    }


async def next_invoice_no() -> str:
    global MEMORY_COUNTER
    if db is None:
        MEMORY_COUNTER += 1
        return f"IMYS-{MEMORY_COUNTER:03d}"

    counter = await db.counters.find_one_and_update(
        {"_id": "invoice_no"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = int(counter.get("seq", 1))
    return f"IMYS-{seq:03d}"


def to_object_id(client_id: str) -> ObjectId:
    try:
        return ObjectId(client_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid client id") from exc


def find_memory_client(client_id: str) -> Optional[Dict[str, Any]]:
    for client in MEMORY_CLIENTS:
        if str(client.get("id")) == str(client_id):
            return client
    return None


@app.get("/")
async def home():
    return {"ok": True, "message": "I M Your Solution backend is running"}


@app.get("/api/health")
async def health():
    if db is None:
        return {
            "ok": True,
            "apiRunning": True,
            "dbConnected": False,
            "message": "API running. Add MONGO_URL in Render for permanent database storage.",
        }

    try:
        await db.command("ping")
        return {"ok": True, "apiRunning": True, "dbConnected": True}
    except Exception as exc:
        return {"ok": True, "apiRunning": True, "dbConnected": False, "error": str(exc)}


@app.post("/api/login")
async def login(payload: LoginRequest):
    if payload.username == ADMIN_USERNAME and payload.password == ADMIN_PASSWORD:
        return {"ok": True, "username": payload.username}
    raise HTTPException(status_code=401, detail="Invalid username or password")


@app.get("/api/clients")
async def list_clients():
    if db is None:
        return [serialize_client(doc) for doc in sorted(MEMORY_CLIENTS, key=lambda x: x.get("createdAt", ""), reverse=True)]

    docs = await db.clients.find().sort("createdAt", -1).to_list(length=5000)
    return [serialize_client(doc) for doc in docs]


@app.post("/api/clients")
async def create_client(payload: ClientIn):
    data = payload.model_dump()
    current_time = now_iso()
    data["invoiceNo"] = await next_invoice_no()
    data["createdAt"] = current_time
    data["updatedAt"] = current_time

    if db is None:
        data["id"] = str(ObjectId())
        MEMORY_CLIENTS.append(data)
        return serialize_client(data)

    result = await db.clients.insert_one(data)
    saved = await db.clients.find_one({"_id": result.inserted_id})
    return serialize_client(saved)


@app.put("/api/clients/{client_id}")
async def update_client(client_id: str, payload: ClientIn):
    data = payload.model_dump()
    data["updatedAt"] = now_iso()

    if db is None:
        existing = find_memory_client(client_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Client not found")
        data["id"] = existing.get("id")
        data["invoiceNo"] = existing.get("invoiceNo", "")
        data["createdAt"] = existing.get("createdAt", "")
        existing.clear()
        existing.update(data)
        return serialize_client(existing)

    oid = to_object_id(client_id)
    existing = await db.clients.find_one({"_id": oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Client not found")

    data["invoiceNo"] = existing.get("invoiceNo", "")
    data["createdAt"] = existing.get("createdAt", "")
    await db.clients.update_one({"_id": oid}, {"$set": data})
    updated = await db.clients.find_one({"_id": oid})
    return serialize_client(updated)


@app.patch("/api/clients/{client_id}/status")
async def update_status(client_id: str, payload: ClientStatusUpdate):
    if db is None:
        existing = find_memory_client(client_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Client not found")
        existing["status"] = payload.status
        existing["updatedAt"] = now_iso()
        return serialize_client(existing)

    oid = to_object_id(client_id)
    existing = await db.clients.find_one({"_id": oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Client not found")

    await db.clients.update_one(
        {"_id": oid},
        {"$set": {"status": payload.status, "updatedAt": now_iso()}},
    )
    updated = await db.clients.find_one({"_id": oid})
    return serialize_client(updated)


@app.delete("/api/clients/{client_id}")
async def delete_client(client_id: str):
    if db is None:
        existing = find_memory_client(client_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Client not found")
        MEMORY_CLIENTS.remove(existing)
        return {"ok": True}

    oid = to_object_id(client_id)
    result = await db.clients.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"ok": True}


@app.get("/api/dashboard")
async def dashboard():
    if db is None:
        docs = MEMORY_CLIENTS
    else:
        docs = await db.clients.find().to_list(length=5000)

    total_clients = len(docs)
    total_amount = sum(float(doc.get("amount", 0) or 0) for doc in docs)
    paid_amount = sum(float(doc.get("amount", 0) or 0) for doc in docs if doc.get("status") == "Paid")
    pending_amount = sum(float(doc.get("amount", 0) or 0) for doc in docs if doc.get("status") != "Paid")
    paid_count = sum(1 for doc in docs if doc.get("status") == "Paid")
    pending_count = sum(1 for doc in docs if doc.get("status") == "Pending")
    sent_count = sum(1 for doc in docs if doc.get("status") == "Sent")

    return {
        "totalClients": total_clients,
        "totalAmount": total_amount,
        "paidAmount": paid_amount,
        "pendingAmount": pending_amount,
        "paidCount": paid_count,
        "pendingCount": pending_count,
        "sentCount": sent_count,
    }
