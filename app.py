import os
import json
import stripe
import base64
import io
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pypdf import PdfReader
import google.generativeai as genai
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv()

app = FastAPI(title="AutoFacture SaaS API")

# Serve the static HTML frontend
app.mount("/static", StaticFiles(directory="static"), name="static")

# Allow CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize API Clients
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Initialize Supabase
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key) if supabase_url and supabase_key else None

def extract_json_from_gemini(text_content: str):
    prompt = f"""
    Tu es un assistant comptable expert. Analyse le texte de cette facture et extrais les informations au format JSON strict.
    Ne renvoie RIEN D'AUTRE que l'objet JSON.
    
    - artisan_nom: (nom)
    - client_nom: (nom)
    - date_facture: (format YYYY-MM-DD)
    - montant_ht: (nombre)
    - montant_ttc: (nombre)
    - tva: (nombre)
    
    Texte de la facture :
    {text_content}
    """
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content(prompt)
    
    raw_response = response.text.strip()
    if raw_response.startswith("```json"): raw_response = raw_response[7:]
    if raw_response.startswith("```"): raw_response = raw_response[3:]
    if raw_response.endswith("```"): raw_response = raw_response[:-3]
        
    return json.loads(raw_response.strip())

def to_float(val):
    if isinstance(val, (int, float)): return float(val)
    try: return float(str(val).replace('€', '').replace(',', '.').strip())
    except: return 0.0

@app.get("/")
def read_root():
    return FileResponse("static/index.html")

@app.post("/api/create-checkout-session")
async def create_checkout_session():
    if not stripe.api_key or stripe.api_key == "sk_test_votre_cle_test_stripe_ici":
        raise HTTPException(status_code=400, detail="Clé API Stripe non configurée.")
    try:
        checkout_session = stripe.checkout.Session.create(
            line_items=[{'price_data': {'currency': 'eur','unit_amount': 4900,'product_data': {'name': 'AutoFacture Pro (Mensuel)'},'recurring': {'interval': 'month'}},'quantity': 1}],
            mode='subscription',
            success_url='http://localhost:8000/?success=true',
            cancel_url='http://localhost:8000/?canceled=true',
        )
        return {"url": checkout_session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/extract-invoice")
async def extract_invoice(file: UploadFile = File(...)):
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés.")
    try:
        reader = PdfReader(file.file)
        text_content = "".join(page.extract_text() + "\n" for page in reader.pages)
        if not text_content.strip(): raise HTTPException(status_code=400, detail="Texte illisible.")

        extracted_data = extract_json_from_gemini(text_content)
        
        if supabase:
            try:
                supabase.table('invoices').insert({
                    "filename": file.filename,
                    "artisan_nom": extracted_data.get("artisan_nom", "Inconnu"),
                    "client_nom": extracted_data.get("client_nom", "Inconnu"),
                    "date_facture": extracted_data.get("date_facture", ""),
                    "montant_ht": to_float(extracted_data.get("montant_ht", 0)),
                    "montant_ttc": to_float(extracted_data.get("montant_ttc", 0)),
                    "tva": to_float(extracted_data.get("tva", 0))
                }).execute()
            except Exception as db_err:
                print(f"Erreur DB: {db_err}")
        return {"status": "success", "filename": file.filename, "data": extracted_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur : {str(e)}")

# =================================================================
# NOUVEAU : WEBHOOK EMAIL POUR RECEVOIR LES FACTURES AUTOMATIQUEMENT
# =================================================================
@app.post("/api/inbound-email")
async def inbound_email(request: Request):
    try:
        payload = await request.json()
        
        # Extraire l'expéditeur (pour savoir quel client a envoyé la facture)
        headers = payload.get("headers", {})
        sender_email = headers.get("from", "Expéditeur inconnu")
        
        # Extraire les pièces jointes
        attachments = payload.get("attachments", [])
        if not attachments:
            return {"status": "error", "message": "Aucune pièce jointe trouvée"}

        # Chercher le premier fichier PDF
        pdf_attachment = next((att for att in attachments if att.get("file_name", "").lower().endswith(".pdf")), None)
        if not pdf_attachment:
            return {"status": "error", "message": "Aucun fichier PDF trouvé dans l'email"}

        # Le contenu est envoyé en Base64 par Cloudmailin, on le décode
        pdf_bytes = base64.b64decode(pdf_attachment["content"])
        filename = pdf_attachment.get("file_name", "facture_email.pdf")

        # Lire le PDF en mémoire
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text_content = "".join(page.extract_text() + "\n" for page in reader.pages)
        
        if not text_content.strip():
            return {"status": "error", "message": "Le PDF est vide ou sous forme d'image non lisible"}

        # Envoyer à l'IA
        extracted_data = extract_json_from_gemini(text_content)
        
        # Sauvegarder dans la base de données
        if supabase:
            supabase.table('invoices').insert({
                "filename": f"[Via Email] {filename}",
                "artisan_nom": extracted_data.get("artisan_nom", "Inconnu"),
                "client_nom": extracted_data.get("client_nom", "Inconnu"),
                "date_facture": extracted_data.get("date_facture", ""),
                "montant_ht": to_float(extracted_data.get("montant_ht", 0)),
                "montant_ttc": to_float(extracted_data.get("montant_ttc", 0)),
                "tva": to_float(extracted_data.get("tva", 0))
            }).execute()

        print(f"✅ Facture traitée automatiquement via email depuis {sender_email}")
        return {"status": "success", "message": "Facture traitée et sauvegardée !"}

    except Exception as e:
        print(f"❌ Erreur Webhook Email: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/history")
def get_history():
    if not supabase: return {"status": "error", "detail": "Supabase n'est pas configuré."}
    try:
        response = supabase.table('invoices').select("*").order("created_at", desc=True).execute()
        return {"status": "success", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
