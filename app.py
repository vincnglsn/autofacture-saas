import os
import json
import stripe
from fastapi import FastAPI, UploadFile, File, HTTPException
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

@app.get("/")
def read_root():
    return FileResponse("static/index.html")

@app.post("/api/create-checkout-session")
async def create_checkout_session():
    if not stripe.api_key or stripe.api_key == "sk_test_votre_cle_test_stripe_ici":
        raise HTTPException(status_code=400, detail="Clé API Stripe non configurée.")
        
    try:
        checkout_session = stripe.checkout.Session.create(
            line_items=[
                {
                    'price_data': {
                        'currency': 'eur',
                        'unit_amount': 4900,
                        'product_data': {
                            'name': 'AutoFacture Pro (Mensuel)',
                            'description': 'Extraction illimitée de factures avec notre IA',
                        },
                        'recurring': {'interval': 'month'},
                    },
                    'quantity': 1,
                },
            ],
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
        text_content = ""
        for page in reader.pages:
            text_content += page.extract_text() + "\n"
            
        if not text_content.strip():
            raise HTTPException(status_code=400, detail="Impossible d'extraire le texte.")

        prompt = f"""
        Tu es un assistant comptable expert. Analyse le texte de cette facture et extrais les informations au format JSON strict.
        Ne renvoie RIEN D'AUTRE que l'objet JSON (pas de markdown, pas de texte avant ou après).
        
        Les clés du JSON doivent être exactement :
        - artisan_nom: (nom)
        - client_nom: (nom)
        - date_facture: (format YYYY-MM-DD)
        - montant_ht: (nombre)
        - montant_ttc: (nombre)
        - tva: (nombre)
        
        Texte de la facture :
        {text_content}
        """

        # Utilisation de l'IA Google Gemini (Gratuite)
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        
        # Nettoyage de la réponse (au cas où Gemini rajoute des balises markdown ```json)
        raw_response = response.text.strip()
        if raw_response.startswith("```json"):
            raw_response = raw_response[7:]
        if raw_response.startswith("```"):
            raw_response = raw_response[3:]
        if raw_response.endswith("```"):
            raw_response = raw_response[:-3]
            
        extracted_data = json.loads(raw_response.strip())
        
        # Save to Database if Supabase is configured
        if supabase:
            try:
                def to_float(val):
                    if isinstance(val, (int, float)): return float(val)
                    try: return float(str(val).replace('€', '').replace(',', '.').strip())
                    except: return 0.0

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
        
        return {
            "status": "success",
            "filename": file.filename,
            "data": extracted_data
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur avec Gemini : {str(e)}")

@app.get("/api/history")
def get_history():
    if not supabase:
        return {"status": "error", "detail": "Supabase n'est pas configuré."}
    
    try:
        response = supabase.table('invoices').select("*").order("created_at", desc=True).execute()
        return {"status": "success", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
