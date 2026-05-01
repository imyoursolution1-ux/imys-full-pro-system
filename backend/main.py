from datetime import datetime
import os

from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from pymongo import ReturnDocument


app = FastAPI(title="I M Your Solution API")

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

if not MONGO_URL:
    raise RuntimeError("MONGO_URL is missing")

mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client[DB_NAME]


class LoginRequest(BaseModel):
    username: str
    password: str


class ClientIn(BaseModel):
    name: str
    phone: str
    amount: float
    contract: str = ""
    dueDate: str = ""
    status: str = "Pending"
    recurring: str = ""
    notes: str = ""
    documentName: str = ""
    documentData: str = ""


class ClientStatusUpdate(BaseModel):
    status: str


def serialize_client(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "invoiceNo": doc.get("invoiceNo", ""),
        "name": doc.get("name", ""),
        "phone": doc.get("phone", ""),
        "amount": float(doc.get("amount", 0)),
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


@app.get("/")
async def home():
    return {"message": "Backend running"}


@app.get("/api/health")
async def health():
    try:
        await db.command("ping")
        return {"ok": True, "dbConnected": True}
    except Exception as exc:
        return {"ok": False, "dbConnected": False, "error": str(exc)}


@app.post("/api/login")
async def login(payload: LoginRequest):
    if payload.username == ADMIN_USERNAME and payload.password == ADMIN_PASSWORD:
        return {"ok": True, "username": payload.username}
    raise HTTPException(status_code=401, detail="Invalid username or password")


@app.get("/api/clients")
async def list_clients():
    docs = await db.clients.find().sort("createdAt", -1).to_list(length=5000)
    return [serialize_client(doc) for doc in docs]


@app.post("/api/clients")
async def create_client(payload: ClientIn):
    data = payload.model_dump()
    now = datetime.utcnow().isoformat()
    data["invoiceNo"] = await next_invoice_no()
    data["createdAt"] = now
    data["updatedAt"] = now
    result = await db.clients.insert_one(data)
    saved = await db.clients.find_one({"_id": result.inserted_id})
    return serialize_client(saved)


@app.put("/api/clients/{client_id}")
async def update_client(client_id: str, payload: ClientIn):
    oid = to_object_id(client_id)
    existing = await db.clients.find_one({"_id": oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Client not found")

    data = payload.model_dump()
    data["invoiceNo"] = existing.get("invoiceNo", "")
    data["createdAt"] = existing.get("createdAt", "")
    data["updatedAt"] = datetime.utcnow().isoformat()

    await db.clients.update_one({"_id": oid}, {"$set": data})
    updated = await db.clients.find_one({"_id": oid})
    return serialize_client(updated)


@app.patch("/api/clients/{client_id}/status")
async def update_status(client_id: str, payload: ClientStatusUpdate):
    oid = to_object_id(client_id)
    existing = await db.clients.find_one({"_id": oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Client not found")

    await db.clients.update_one(
        {"_id": oid},
        {"$set": {"status": payload.status, "updatedAt": datetime.utcnow().isoformat()}},
    )
    updated = await db.clients.find_one({"_id": oid})
    return serialize_client(updated)


@app.delete("/api/clients/{client_id}")
async def delete_client(client_id: str):
    oid = to_object_id(client_id)
    result = await db.clients.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"ok": True}


@app.get("/api/dashboard")
async def dashboard():
    docs = await db.clients.find().to_list(length=5000)

    total_clients = len(docs)
    total_amount = sum(float(doc.get("amount", 0)) for doc in docs)
    paid_amount = sum(float(doc.get("amount", 0)) for doc in docs if doc.get("status") == "Paid")
    pending_amount = sum(float(doc.get("amount", 0)) for doc in docs if doc.get("status") != "Paid")

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
