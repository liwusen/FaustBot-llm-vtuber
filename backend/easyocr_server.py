import easyocr
import os
from fastapi import FastAPI
os.chdir(os.path.dirname(os.path.abspath(__file__)))
# byteIO for image bytes handling
PORT = 8000
app = FastAPI()
print("Loading EasyOCR model...")
# load model (languages: English and Chinese)
reader = easyocr.Reader(['en', 'ch_sim'], gpu=True)
print("EasyOCR model loaded.")
# use BytesIO to handle image bytes
from io import BytesIO
from fastapi import File, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image


@app.post("/ocr/")
async def ocr_image(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        image = Image.open(BytesIO(contents)).convert("RGB")
        image_bytes = BytesIO()
        image.save(image_bytes, format='PNG')
        image_bytes = image_bytes.getvalue()

        result = reader.readtext(image_bytes)
        ocr_texts = [res[1] for res in result]

        return JSONResponse(content={"texts": ocr_texts})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    print(f"Starting EasyOCR server on port {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
