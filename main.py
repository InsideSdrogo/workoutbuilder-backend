"""
WorkoutBuilder — Backend FastAPI
Genera PDF schede + gestisce pagamenti Stripe
"""

import os, json, io, datetime, hmac, hashlib
import stripe
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

# ReportLab
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, Table, TableStyle, HRFlowable)

# ── CONFIG ──────────────────────────────────────────
stripe.api_key        = os.environ["STRIPE_SECRET_KEY"]
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]
PRICE_ID              = os.environ["STRIPE_PRICE_ID"]        # es. price_xxx
FRONTEND_URL          = os.environ.get("FRONTEND_URL", "http://localhost:3000")
PDF_PRICE_EUR         = 9.99

app = FastAPI(title="WorkoutBuilder API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── MODELLI ─────────────────────────────────────────
class Exercise(BaseModel):
    id: int
    n: str          # nome
    g: str          # gruppo muscolare
    e: str          # emoji
    series: int
    reps: str
    rest: str
    note: Optional[str] = ""

class Day(BaseModel):
    id: int
    name: str
    exs: List[Exercise]

class WorkoutData(BaseModel):
    meta_title: Optional[str] = ""
    meta_level: Optional[str] = "Intermedio"
    meta_goal:  Optional[str] = "Ipertrofia"
    meta_dur:   Optional[str] = "60 min"
    days: List[Day]

class CheckoutRequest(BaseModel):
    workout_data: WorkoutData
    success_url: Optional[str] = None
    cancel_url:  Optional[str] = None

# ── GENERAZIONE PDF ──────────────────────────────────
W, H = A4

BG     = colors.HexColor('#0d0d0d')
CARD   = colors.HexColor('#1a1a1a')
BORDER = colors.HexColor('#2c2c2c')
BORDER2= colors.HexColor('#383838')
ACC    = colors.HexColor('#b8ff00')
TX     = colors.HexColor('#ececec')
TX2    = colors.HexColor('#999999')
TX3    = colors.HexColor('#555555')

GC = {
    'Petto':      colors.HexColor('#ff6b6b'),
    'Schiena':    colors.HexColor('#4ecdc4'),
    'Spalle':     colors.HexColor('#45b7d1'),
    'Bicipiti':   colors.HexColor('#f9ca24'),
    'Tricipiti':  colors.HexColor('#f0932b'),
    'Gambe':      colors.HexColor('#6ab04c'),
    'Glutei':     colors.HexColor('#ff9ff3'),
    'Addominali': colors.HexColor('#e056fd'),
    'Cardio':     colors.HexColor('#ff7675'),
    'Mobilità':   colors.HexColor('#26de81'),
}
def gc(g): return GC.get(g, TX2)

class DarkDoc(BaseDocTemplate):
    def __init__(self, buf, **kw):
        super().__init__(buf, **kw)
        fr = Frame(12*mm, 14*mm, W-24*mm, H-28*mm, id='main')
        self.addPageTemplates([PageTemplate(id='p', frames=fr, onPage=self._bg)])

    def _bg(self, c, doc):
        c.saveState()
        c.setFillColor(BG);  c.rect(0, 0, W, H, fill=1, stroke=0)
        c.setFillColor(ACC); c.rect(0, H-3, W, 3, fill=1, stroke=0)
        c.setFillColor(TX3); c.setFont('Helvetica', 7)
        c.drawCentredString(W/2, 7*mm, f'WorkoutBuilder  ·  pag. {doc.page}')
        c.restoreState()

def ps(name, **kw):
    d = dict(fontName='Helvetica', fontSize=10, textColor=TX, leading=14)
    d.update(kw); return ParagraphStyle(name, **d)

S_LABEL = ps('lb', fontName='Helvetica-Bold', fontSize=8, textColor=TX3, leading=11)
S_TITLE = ps('ti', fontName='Helvetica-Bold', fontSize=28, textColor=ACC, leading=32, spaceAfter=1*mm)
S_META  = ps('me', fontSize=9, textColor=TX2, leading=12, spaceAfter=4*mm)
S_DAY   = ps('dy', fontName='Helvetica-Bold', fontSize=11, textColor=ACC, leading=15, spaceBefore=5*mm, spaceAfter=2*mm)
S_NUM   = ps('nu', fontName='Helvetica-Bold', fontSize=14, textColor=ACC, leading=16,
             alignment=1)  # CENTER
S_NAME  = ps('na', fontName='Helvetica-Bold', fontSize=10, textColor=TX, leading=13)
S_GRP   = ps('gr', fontName='Helvetica-Bold', fontSize=7, textColor=TX2, leading=10)
S_NOTE  = ps('no', fontName='Helvetica-Oblique', fontSize=8,
             textColor=colors.HexColor('#aaaaaa'), leading=11)
S_PL    = ps('pl', fontName='Helvetica-Bold', fontSize=7, textColor=TX3, leading=9, alignment=1)
S_PV    = ps('pv', fontName='Helvetica-Bold', fontSize=11, textColor=TX, leading=13, alignment=1)

def build_pdf(data: WorkoutData) -> bytes:
    buf = io.BytesIO()
    doc = DarkDoc(buf, pagesize=A4,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=14*mm, bottomMargin=16*mm)

    avail = W - 24*mm
    CW = [9*mm, None, 16*mm, 20*mm, 16*mm]
    CW[1] = avail - sum(x for x in CW if x)

    story = []

    # Header
    story.append(Paragraph('SCHEDA DI ALLENAMENTO', S_LABEL))
    title = (data.meta_title or '').strip() or 'Scheda Personalizzata'
    story.append(Paragraph(title.upper(), S_TITLE))
    today = datetime.date.today().strftime('%d/%m/%Y')
    story.append(Paragraph(
        f"{data.meta_level}  ·  {data.meta_goal}  ·  {data.meta_dur}  ·  {today}",
        S_META))
    story.append(HRFlowable(width='100%', thickness=1, color=BORDER2, spaceAfter=2*mm))

    for day in data.days:
        if not day.exs:
            continue
        story.append(Paragraph(
            f"{day.name.upper()}  <font color='#555555' size='8'>— {len(day.exs)} esercizi</font>",
            S_DAY))

        for i, ex in enumerate(day.exs):
            c = gc(ex.g)
            grp_style = ParagraphStyle('gs', fontName='Helvetica-Bold',
                fontSize=7, textColor=c, leading=10)

            info = [Paragraph(ex.n, S_NAME),
                    Paragraph(ex.g.upper(), grp_style)]
            if (ex.note or '').strip():
                info.append(Paragraph(f"↳  {ex.note}", S_NOTE))

            row = [
                [Paragraph(f"{i+1:02d}", S_NUM)],
                info,
                [Paragraph('SERIE', S_PL), Paragraph(str(ex.series), S_PV)],
                [Paragraph('REPS',  S_PL), Paragraph(ex.reps, S_PV)],
                [Paragraph('REC.',  S_PL), Paragraph(ex.rest, S_PV)],
            ]

            t = Table([row], colWidths=CW)
            t.setStyle(TableStyle([
                ('BACKGROUND',    (0,0),(-1,-1), CARD),
                ('TOPPADDING',    (0,0),(-1,-1), 6),
                ('BOTTOMPADDING', (0,0),(-1,-1), 7),
                ('LEFTPADDING',   (0,0),(0,-1),  0),
                ('RIGHTPADDING',  (0,0),(0,-1),  4),
                ('LEFTPADDING',   (1,0),(1,-1),  7),
                ('VALIGN',        (0,0),(-1,-1), 'MIDDLE'),
                ('VALIGN',        (1,0),(1,-1),  'TOP'),
                ('LINEBEFORE',    (0,0),(0,-1),  3, c),
                ('LINEBEFORE',    (2,0),(-1,-1), 0.5, BORDER),
                ('BOX',           (0,0),(-1,-1), 0.5, BORDER),
            ]))
            story.append(t)
            story.append(Spacer(1, 2*mm))

        story.append(HRFlowable(width='100%', thickness=0.5,
            color=BORDER, spaceBefore=2*mm, spaceAfter=0))

    doc.build(story)
    return buf.getvalue()


# ── ENDPOINT: genera PDF gratis (preview / test) ────
@app.post("/preview-pdf")
async def preview_pdf(data: WorkoutData):
    """Genera un PDF di anteprima (prime 3 righe per giorno — watermark)"""
    # Per ora restituisce il PDF completo — in produzione puoi limitarlo
    pdf_bytes = build_pdf(data)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=anteprima.pdf"}
    )


# ── ENDPOINT: crea sessione Stripe Checkout ──────────
@app.post("/crea-checkout")
async def crea_checkout(req: CheckoutRequest):
    workout_json = req.workout_data.model_dump_json()

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price": PRICE_ID,
            "quantity": 1,
        }],
        mode="payment",
        success_url=req.success_url or f"{FRONTEND_URL}/successo?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=req.cancel_url or f"{FRONTEND_URL}",
        metadata={"workout_data": workout_json[:4000]},  # Stripe limite 500 char per key
        payment_intent_data={
            "metadata": {"workout_data": workout_json[:4000]}
        }
    )
    return {"checkout_url": session.url, "session_id": session.id}


# ── ENDPOINT: scarica PDF dopo pagamento ─────────────
@app.get("/scarica-pdf/{session_id}")
async def scarica_pdf(session_id: str):
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Sessione non trovata")

    if session.payment_status != "paid":
        raise HTTPException(status_code=402, detail="Pagamento non completato")

    workout_json = session.metadata.get("workout_data")
    if not workout_json:
        raise HTTPException(status_code=400, detail="Dati scheda non trovati")

    data = WorkoutData(**json.loads(workout_json))
    pdf_bytes = build_pdf(data)

    filename = (data.meta_title or "scheda").replace(" ", "-").lower() + ".pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ── WEBHOOK Stripe (opzionale — per log/email) ───────
@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Webhook non valido")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        print(f"✅ Pagamento completato: {session['id']}")
        # Qui puoi inviare email con il link PDF, loggare su DB, ecc.

    return {"ok": True}


# ── HEALTH CHECK ─────────────────────────────────────
@app.get("/")
def root():
    return {"status": "ok", "service": "WorkoutBuilder API"}
