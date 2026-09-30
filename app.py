import os
import json
import stripe
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pypdf import PdfReader
from openai import OpenAI
from dotenv import load_dotenv

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
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

@app.get("/")
def read_root():
    return FileResponse("static/index.html")

@app.post("/api/create-checkout-session")
async def create_checkout_session():
    """
    Crée une session de paiement Stripe pour l'abonnement à 49€/mois.
    """
    if not stripe.api_key or stripe.api_key == "sk_test_votre_cle_test_stripe_ici":
        raise HTTPException(status_code=400, detail="Clé API Stripe non configurée dans le fichier .env")
        
    try:
        checkout_session = stripe.checkout.Session.create(
            line_items=[
                {
                    'price_data': {
                        'currency': 'eur',
                        'unit_amount': 4900, # 49.00 EUR (en centimes)
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
            # En production, on utiliserait le vrai nom de domaine
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
        Tu es un assistant comptable expert. Analyse le texte de cette facture et extrais les informations au format JSON :
        - artisan_nom: (nom)
        - client_nom: (nom)
        - date_facture: (format YYYY-MM-DD)
        - montant_ht: (nombre)
        - montant_ttc: (nombre)
        - tva: (nombre)
        
        Texte :
        {text_content}
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Tu dois répondre UNIQUEMENT avec un objet JSON valide."},
                {"role": "user", "content": prompt}
            ],
            response_format={ "type": "json_object" }
        )

        extracted_data = json.loads(response.choices[0].message.content)
        
        return {
            "status": "success",
            "filename": file.filename,
            "data": extracted_data
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur : {str(e)}")
