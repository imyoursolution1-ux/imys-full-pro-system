from datetime import datetime
import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from bson import ObjectId
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo import ReturnDocument
except Exception:  # dependencies may be unavailable during static checks
    ObjectId = None
    AsyncIOMotorClient = None
    ReturnDocument = None

app = FastAPI(title="IMYourSolution Payment Dashboard API")

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
WEBSITE = "IMYOURSOLUTION.COM"
WEBSITE_LINK = "https://imyoursolution.com"
DEFAULT_UPI_ID = os.getenv("DEFAULT_UPI_ID", "9529615972@HDFC").strip()

mongo_client = None
db = None
if MONGO_URL and AsyncIOMotorClient:
    mongo_client = AsyncIOMotorClient(MONGO_URL)
    db = mongo_client[DB_NAME]

# Safe in-memory fallback so backend does not crash when Mongo is not configured.
MEMORY_CLIENTS: list[dict] = []
MEMORY_SEQ = 0

class LoginRequest(BaseModel):
    username: str
    password: str

class ClientIn(BaseModel):
    name: str
    phone: str = ""
    amount: float = 0
    contract: str = ""
    dueDate: str = ""
    status: str = "Pending"
    recurring: str = ""
    upi: str = ""
    messageMode: str = "WhatsApp"
    notes: str = ""
    documentName: str = ""
    documentData: str = ""

class ClientStatusUpdate(BaseModel):
    status: str

class MessageRequest(BaseModel):
    name: str = "Client"
    phone: str = ""
    amount: float = 0
    invoiceNo: str = ""
    contract: str = ""
    dueDate: str = ""
    status: str = "Pending"
    upi: str = ""


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def next_memory_invoice() -> str:
    global MEMORY_SEQ
    MEMORY_SEQ += 1
    return f"IMYS-{MEMORY_SEQ:03d}"


def serialize_client(doc: dict) -> dict:
    raw_id = doc.get("_id", doc.get("id", ""))
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
        "upi": doc.get("upi", ""),
        "messageMode": doc.get("messageMode", "WhatsApp"),
        "notes": doc.get("notes", ""),
        "documentName": doc.get("documentName", ""),
        "documentData": doc.get("documentData", ""),
        "createdAt": doc.get("createdAt", ""),
        "updatedAt": doc.get("updatedAt", ""),
    }

async def next_invoice_no() -> str:
    if db is None:
        return next_memory_invoice()
    counter = await db.counters.find_one_and_update(
        {"_id": "invoice_no"}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER
    )
    seq = int(counter.get("seq", 1))
    return f"IMYS-{seq:03d}"


def to_object_id(client_id: str):
    if ObjectId is None:
        raise HTTPException(status_code=400, detail="ObjectId dependency missing")
    try:
        return ObjectId(client_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid client id") from exc


def build_message(c: MessageRequest) -> str:
    upi_id = (c.upi or DEFAULT_UPI_ID).strip()
    parts = [
        f"Dear {c.name or 'Client'},",
        "",
        "Payment request from I'M Your Solution.",
        f"Website: {WEBSITE}",
        f"Link: {WEBSITE_LINK}",
        "",
        f"Service / Reason: {c.contract or '-'}",
        f"Invoice No: {c.invoiceNo or '-'}",
        f"Amount Due: ₹{c.amount or 0}",
        f"Due Date: {c.dueDate or '-'}",
        f"Status: {c.status or 'Pending'}",
    ]
    if upi_id:
        parts += ["", f"Payment UPI ID: {upi_id}"]
    parts += ["", "Please complete your payment and share screenshot after payment.", "", "Thank you,", "I'M Your Solution", WEBSITE]
    return "\n".join(parts)

@app.get("/")
async def home():
    return {"ok": True, "message": "IMYourSolution backend running", "dbConnected": db is not None}

@app.get("/api/health")
async def health():
    if db is None:
        return {"ok": True, "dbConnected": False, "mode": "memory", "note": "MONGO_URL not set; backend will still run."}
    try:
        await db.command("ping")
        return {"ok": True, "dbConnected": True, "mode": "mongo"}
    except Exception as exc:
        return {"ok": False, "dbConnected": False, "error": str(exc)}

@app.post("/api/login")
async def login(payload: LoginRequest):
    if payload.username == ADMIN_USERNAME and payload.password == ADMIN_PASSWORD:
        return {"ok": True, "username": payload.username}
    raise HTTPException(status_code=401, detail="Invalid username or password")

@app.post("/api/build-message")
async def api_build_message(payload: MessageRequest):
    return {"message": build_message(payload), "website": WEBSITE, "upiLinkUsed": False}

@app.get("/api/clients")
async def list_clients():
    if db is None:
        return [serialize_client(x) for x in MEMORY_CLIENTS]
    docs = await db.clients.find().sort("createdAt", -1).to_list(length=5000)
    return [serialize_client(doc) for doc in docs]

@app.post("/api/clients")
async def create_client(payload: ClientIn):
    data = payload.model_dump()
    data["invoiceNo"] = await next_invoice_no()
    data["createdAt"] = now_iso()
    data["updatedAt"] = now_iso()
    if db is None:
        data["id"] = data["invoiceNo"]
        MEMORY_CLIENTS.insert(0, data)
        return serialize_client(data)
    result = await db.clients.insert_one(data)
    saved = await db.clients.find_one({"_id": result.inserted_id})
    return serialize_client(saved)

@app.put("/api/clients/{client_id}")
async def update_client(client_id: str, payload: ClientIn):
    data = payload.model_dump()
    data["updatedAt"] = now_iso()
    if db is None:
        for i, item in enumerate(MEMORY_CLIENTS):
            if item.get("id") == client_id or item.get("invoiceNo") == client_id:
                data["id"] = item.get("id", client_id)
                data["invoiceNo"] = item.get("invoiceNo", client_id)
                data["createdAt"] = item.get("createdAt", now_iso())
                MEMORY_CLIENTS[i] = data
                return serialize_client(data)
        raise HTTPException(status_code=404, detail="Client not found")
    oid = to_object_id(client_id)
    existing = await db.clients.find_one({"_id": oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Client not found")
    data["invoiceNo"] = existing.get("invoiceNo", "")
    data["createdAt"] = existing.get("createdAt", "")
    await db.clients.update_one({"_id": oid}, {"$set": data})
    return serialize_client(await db.clients.find_one({"_id": oid}))

@app.patch("/api/clients/{client_id}/status")
async def update_status(client_id: str, payload: ClientStatusUpdate):
    if db is None:
        for item in MEMORY_CLIENTS:
            if item.get("id") == client_id or item.get("invoiceNo") == client_id:
                item["status"] = payload.status
                item["updatedAt"] = now_iso()
                return serialize_client(item)
        raise HTTPException(status_code=404, detail="Client not found")
    oid = to_object_id(client_id)
    await db.clients.update_one({"_id": oid}, {"$set": {"status": payload.status, "updatedAt": now_iso()}})
    updated = await db.clients.find_one({"_id": oid})
    if not updated:
        raise HTTPException(status_code=404, detail="Client not found")
    return serialize_client(updated)

@app.delete("/api/clients/{client_id}")
async def delete_client(client_id: str):
    if db is None:
        before = len(MEMORY_CLIENTS)
        MEMORY_CLIENTS[:] = [x for x in MEMORY_CLIENTS if x.get("id") != client_id and x.get("invoiceNo") != client_id]
        if len(MEMORY_CLIENTS) == before:
            raise HTTPException(status_code=404, detail="Client not found")
        return {"ok": True}
    result = await db.clients.delete_one({"_id": to_object_id(client_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"ok": True}

@app.get("/api/dashboard")
async def dashboard():
    docs = MEMORY_CLIENTS if db is None else await db.clients.find().to_list(length=5000)
    total_clients = len(docs)
    total_amount = sum(float(d.get("amount", 0) or 0) for d in docs)
    paid_amount = sum(float(d.get("amount", 0) or 0) for d in docs if d.get("status") == "Paid")
    pending_amount = sum(float(d.get("amount", 0) or 0) for d in docs if d.get("status") != "Paid")
    return {
        "totalClients": total_clients,
        "totalAmount": total_amount,
        "paidAmount": paid_amount,
        "pendingAmount": pending_amount,
        "paidCount": sum(1 for d in docs if d.get("status") == "Paid"),
        "pendingCount": sum(1 for d in docs if d.get("status") == "Pending"),
        "sentCount": sum(1 for d in docs if d.get("status") == "Sent"),
    }
