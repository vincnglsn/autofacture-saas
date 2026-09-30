from reportlab.pdfgen import canvas

def create_invoice():
    c = canvas.Canvas("facture_test.pdf")
    c.drawString(100, 800, "FACTURE N° 2026-001")
    c.drawString(100, 780, "Date: 2026-09-30")
    
    c.drawString(100, 740, "ARTISAN : PLOMBERIE DUPONT")
    c.drawString(100, 720, "12 Rue de la Paix, 75000 Paris")
    
    c.drawString(400, 740, "CLIENT : Agence Immo Dupont")
    c.drawString(400, 720, "15 Avenue des Champs, 75008 Paris")
    
    c.drawString(100, 680, "Désignation : Réparation fuite d'eau appartement 4B")
    
    c.drawString(100, 600, "Montant HT : 250.00 EUR")
    c.drawString(100, 580, "TVA (20%) : 50.00 EUR")
    c.drawString(100, 560, "Montant TTC : 300.00 EUR")
    
    c.drawString(100, 500, "Merci de votre confiance.")
    
    c.save()

if __name__ == "__main__":
    create_invoice()
