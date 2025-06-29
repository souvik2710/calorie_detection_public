import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai
import os
from PIL import Image
import base64
import io
import logging
from datetime import datetime
import json
import requests
from google.cloud import speech
from google.cloud import texttospeech
from google.cloud import vision
from google.cloud import translate_v2 as translate
import pandas as pd
from google.cloud import bigquery
import tempfile
import speech_recognition as sr
import pyttsx3
import threading

# Load environment variables
load_dotenv()

# Configure logging for token tracking
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('nutrition_app_tokens.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configure the Google APIs
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# Initialize models and clients
model = genai.GenerativeModel("gemini-1.5-flash")

# Optional: Initialize other Google Cloud services (requires credentials)
try:
    vision_client = vision.ImageAnnotatorClient()
    translate_client = translate.Client()
    tts_client = texttospeech.TextToSpeechClient()
    speech_client = speech.SpeechClient()
    bq_client = bigquery.Client()
    GOOGLE_CLOUD_ENABLED = True
except Exception as e:
    GOOGLE_CLOUD_ENABLED = False
    logger.warning(f"Google Cloud services not configured: {e}")

def encode_image(image):
    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return img_str

def vision_ai_analysis(image):
    """Enhanced image analysis using Vision AI for object detection"""
    if not GOOGLE_CLOUD_ENABLED:
        return None
    
    try:
        # Convert PIL image to bytes
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='PNG')
        img_byte_arr = img_byte_arr.getvalue()
        
        # Create Vision API image object
        vision_image = vision.Image(content=img_byte_arr)
        
        # Perform object detection
        objects = vision_client.object_localization(image=vision_image).localized_object_annotations
        
        # Perform label detection for food items
        labels = vision_client.label_detection(image=vision_image).label_annotations
        
        food_objects = []
        for obj in objects:
            if any(food_term in obj.name.lower() for food_term in ['food', 'fruit', 'vegetable', 'meat', 'bread', 'drink']):
                food_objects.append({
                    'name': obj.name,
                    'confidence': obj.score,
                    'location': [(vertex.x, vertex.y) for vertex in obj.bounding_poly.normalized_vertices]
                })
        
        food_labels = [label.description for label in labels if label.score > 0.7]
        
        return {'objects': food_objects, 'labels': food_labels}
    except Exception as e:
        logger.error(f"Vision AI analysis failed: {e}")
        return None

def translate_response(text, target_language='es'):
    """Translate nutrition response to different languages"""
    if not GOOGLE_CLOUD_ENABLED:
        return text
    
    try:
        result = translate_client.translate(text, target_language=target_language)
        return result['translatedText']
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return text

def text_to_speech(text, language_code='en-US'):
    """Convert nutrition analysis to speech"""
    if not GOOGLE_CLOUD_ENABLED:
        return None
    
    try:
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code=language_code,
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        
        response = tts_client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        
        return response.audio_content
    except Exception as e:
        logger.error(f"Text-to-speech failed: {e}")
        return None

def save_to_bigquery(analysis_data):
    """Save nutrition analysis to BigQuery for analytics"""
    if not GOOGLE_CLOUD_ENABLED:
        return False
    
    try:
        dataset_id = "nutrition_analytics"
        table_id = "meal_analyses"
        
        table_ref = bq_client.dataset(dataset_id).table(table_id)
        
        rows_to_insert = [{
            'timestamp': datetime.now().isoformat(),
            'analysis_text': analysis_data['text'],
            'calories': analysis_data.get('calories', 0),
            'protein': analysis_data.get('protein', 0),
            'carbs': analysis_data.get('carbs', 0),
            'fat': analysis_data.get('fat', 0),
            'health_rating': analysis_data.get('health_rating', 'Unknown')
        }]
        
        errors = bq_client.insert_rows_json(table_ref, rows_to_insert)
        if not errors:
            logger.info("Data saved to BigQuery successfully")
            return True
        else:
            logger.error(f"BigQuery insertion errors: {errors}")
            return False
    except Exception as e:
        logger.error(f"BigQuery save failed: {e}")
        return False

def get_nutritional_info(image_path, use_vision_ai=False, target_language='en'):
    image = Image.open(image_path)
    img_str = encode_image(image)
    
    # Enhanced analysis with Vision AI
    vision_data = None
    if use_vision_ai:
        vision_data = vision_ai_analysis(image)
    
    # Enhanced system prompt
    system_prompt = f"""You are a certified nutritionist and registered dietitian with 15+ years of experience in food analysis and dietary assessment. Your expertise includes macro and micronutrient analysis, portion estimation, and health evaluation.

{f"ADDITIONAL CONTEXT: Vision AI detected these food items: {vision_data['labels'] if vision_data else 'None'}. Use this to enhance your analysis." if vision_data else ""}

INSTRUCTIONS:
1. Analyze the food image with precision and identify all visible food items
2. Estimate portion sizes using standard serving measurements
3. Calculate nutritional values based on recognized food databases (USDA, etc.)
4. Provide specific numerical values, not ranges
5. Give actionable dietary recommendations
6. Be concise but comprehensive

OUTPUT FORMAT:
**FOOD IDENTIFICATION:**
- List each food item with estimated portion size

**NUTRITIONAL BREAKDOWN:**
- Total Calories: [exact number] kcal
- Total Protein: [number] g
- Total Carbohydrates: [number] g
- Total Fat: [number] g
- Total Fiber: [number] g
- Total Sugar: [number] g
- Sodium: [number] mg
- Key Vitamins & Minerals: [list top 3-4]

**HEALTH ASSESSMENT:**
- Overall Health Rating: [Excellent/Good/Fair/Poor]
- Justification: [2-3 specific reasons]

**PROFESSIONAL RECOMMENDATIONS:**
- Immediate suggestions for meal improvement
- Portion adjustments if needed
- Complementary foods to add nutritional balance

EXAMPLES OF GOOD ANALYSIS:
- "Grilled chicken breast (4 oz) with steamed broccoli (1 cup) and brown rice (0.5 cup)"
- "Total Calories: 425 kcal, Protein: 35g, Carbs: 45g, Fat: 8g"
- "Health Rating: Excellent - balanced macros, high protein, fiber-rich vegetables"

Analyze this meal image and provide the complete nutritional assessment following the format above. No disclaimers or liability statements."""

    # Log input token count estimation
    input_text_length = len(system_prompt)
    estimated_input_tokens = input_text_length // 4
    
    logger.info(f"=== API CALL START ===")
    logger.info(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Input text length: {input_text_length} characters")
    logger.info(f"Estimated input tokens: {estimated_input_tokens}")
    logger.info(f"Vision AI enabled: {use_vision_ai}")
    logger.info(f"Target language: {target_language}")
    
    try:
        response = model.generate_content(
            contents=[
                {"mime_type": "image/png", "data": img_str},
                {"text": system_prompt}
            ]
        )
        
        # Log token usage information
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            usage = response.usage_metadata
            logger.info(f"ACTUAL TOKEN USAGE:")
            logger.info(f"Input tokens: {getattr(usage, 'prompt_token_count', 'N/A')}")
            logger.info(f"Output tokens: {getattr(usage, 'candidates_token_count', 'N/A')}")
            logger.info(f"Total tokens: {getattr(usage, 'total_token_count', 'N/A')}")
        else:
            output_text = ""
            if hasattr(response, 'candidates') and response.candidates:
                for candidate in response.candidates:
                    for part in candidate.content.parts:
                        output_text += part.text
            
            output_length = len(output_text)
            estimated_output_tokens = output_length // 4
            estimated_total_tokens = estimated_input_tokens + estimated_output_tokens
            
            logger.info(f"ESTIMATED TOKEN USAGE (API metadata not available):")
            logger.info(f"Input tokens: ~{estimated_input_tokens}")
            logger.info(f"Output tokens: ~{estimated_output_tokens}")
            logger.info(f"Total tokens: ~{estimated_total_tokens}")
            logger.info(f"Output text length: {output_length} characters")
        
        logger.info(f"=== API CALL END ===")
        return response, vision_data
        
    except Exception as e:
        logger.error(f"API call failed: {str(e)}")
        logger.info(f"=== API CALL FAILED ===")
        raise e

def extract_nutrition_values(text):
    """Extract numerical values from nutrition text for BigQuery storage"""
    import re
    
    patterns = {
        'calories': r'Total Calories:\s*(\d+)',
        'protein': r'Total Protein:\s*(\d+(?:\.\d+)?)',
        'carbs': r'Total Carbohydrates:\s*(\d+(?:\.\d+)?)',
        'fat': r'Total Fat:\s*(\d+(?:\.\d+)?)',
        'health_rating': r'Overall Health Rating:\s*(\w+)'
    }
    
    values = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            if key == 'health_rating':
                values[key] = match.group(1)
            else:
                values[key] = float(match.group(1))
        else:
            values[key] = 0 if key != 'health_rating' else 'Unknown'
    
    return values

def display_response(response, vision_data=None, target_language='en'):
    st.subheader("🥗 Professional Nutritional Analysis")
    try:
        response_text = ""
        for candidate in response.candidates:
            for part in candidate.content.parts:
                response_text += part.text
        
        # Translate if needed
        if target_language != 'en':
            response_text = translate_response(response_text, target_language)
        
        # Enhanced display with better formatting
        content = response_text
        if "FOOD IDENTIFICATION:" in content:
            content = content.replace("**FOOD IDENTIFICATION:**", "### 🍽️ Food Identification")
        if "NUTRITIONAL BREAKDOWN:" in content:
            content = content.replace("**NUTRITIONAL BREAKDOWN:**", "### 📊 Nutritional Breakdown")
        if "HEALTH ASSESSMENT:" in content:
            content = content.replace("**HEALTH ASSESSMENT:**", "### 🏥 Health Assessment")
        if "PROFESSIONAL RECOMMENDATIONS:" in content:
            content = content.replace("**PROFESSIONAL RECOMMENDATIONS:**", "### 💡 Professional Recommendations")
        
        st.markdown(content)
        
        # Display Vision AI results if available
        if vision_data and vision_data['objects']:
            with st.expander("🤖 Vision AI Detection Results"):
                st.write("**Detected Food Objects:**")
                for obj in vision_data['objects']:
                    st.write(f"- {obj['name']} (Confidence: {obj['confidence']:.2f})")
        
        # Extract nutritional values for BigQuery
        nutrition_values = extract_nutrition_values(response_text)
        nutrition_values['text'] = response_text
        
        # Save to BigQuery
        if st.session_state.get('save_to_bq', False):
            save_success = save_to_bigquery(nutrition_values)
            if save_success:
                st.success("✅ Analysis saved to BigQuery for analytics")
        
        return response_text, nutrition_values
        
    except AttributeError as e:
        st.error(f"Error in accessing the response attributes: {e}")
        st.info("Please try uploading the image again or check your API configuration.")
        return None, None

# Streamlit UI with enhanced styling
st.set_page_config(
    page_title="AI Nutritionist - Multi-AI Meal Analysis",
    page_icon="🥗",
    layout="wide"
)

st.title("🥗 Multi-AI Nutritionist - Advanced Meal Analysis")
st.markdown("*Powered by Gemini, Vision AI, Translation, Text-to-Speech & BigQuery Analytics*")

# Initialize session state
if 'analysis_history' not in st.session_state:
    st.session_state.analysis_history = []

# Create columns for better layout
col1, col2 = st.columns([2, 1])

with col1:
    uploaded_image = st.file_uploader(
        "📸 Upload a clear image of your meal", 
        type=["jpg", "jpeg", "png"],
        help="For best results, ensure good lighting and all food items are visible"
    )
    
    # AI Enhancement Options
    st.subheader("🤖 AI Enhancement Options")
    col_ai1, col_ai2 = st.columns(2)
    
    with col_ai1:
        use_vision_ai = st.checkbox("🔍 Use Vision AI Detection", 
                                   value=False, 
                                   help="Enhanced food detection using Google Vision AI")
        
        target_language = st.selectbox("🌍 Analysis Language", 
                                     ['en', 'es', 'fr', 'de', 'it', 'pt', 'hi', 'zh'],
                                     format_func=lambda x: {
                                         'en': '🇺🇸 English',
                                         'es': '🇪🇸 Spanish', 
                                         'fr': '🇫🇷 French',
                                         'de': '🇩🇪 German',
                                         'it': '🇮🇹 Italian',
                                         'pt': '🇵🇹 Portuguese',
                                         'hi': '🇮🇳 Hindi',
                                         'zh': '🇨🇳 Chinese'
                                     }[x])
    
    with col_ai2:
        enable_tts = st.checkbox("🔊 Text-to-Speech", 
                               value=False,
                               help="Convert analysis to audio")
        
        st.session_state.save_to_bq = st.checkbox("📊 Save to BigQuery", 
                                                 value=False,
                                                 help="Store analysis for future analytics")
    
    if uploaded_image is not None:
        st.image(uploaded_image, caption="Your Meal", use_column_width=True)
        
        if st.button("🔍 Analyze with Multi-AI", type="primary"):
            with st.spinner("🧠 Multi-AI system analyzing your meal..."):
                try:
                    response, vision_data = get_nutritional_info(
                        uploaded_image, 
                        use_vision_ai=use_vision_ai,
                        target_language=target_language
                    )
                    
                    response_text, nutrition_values = display_response(
                        response, 
                        vision_data, 
                        target_language
                    )
                    
                    # Text-to-Speech
                    if enable_tts and response_text:
                        with st.spinner("🔊 Generating audio..."):
                            audio_content = text_to_speech(response_text, 
                                                         f"{target_language}-US" if target_language == 'en' else f"{target_language}")
                            if audio_content:
                                st.audio(audio_content, format='audio/mp3')
                    
                    # Save to history
                    st.session_state.analysis_history.append({
                        'timestamp': datetime.now(),
                        'nutrition_values': nutrition_values,
                        'language': target_language,
                        'vision_ai_used': use_vision_ai
                    })
                    
                except Exception as e:
                    st.error(f"Analysis failed: {str(e)}")
                    st.info("Please check your API configurations and internet connection.")

with col2:
    st.sidebar.header("📋 How to Use Multi-AI")
    st.sidebar.markdown("""
    **🤖 AI Services Available:**
    - **Gemini**: Core nutrition analysis
    - **Vision AI**: Enhanced food detection  
    - **Translation**: 8 languages supported
    - **Text-to-Speech**: Audio analysis
    - **BigQuery**: Analytics & history
    
    **📊 Advanced Features:**
    - Multi-language support
    - Audio output
    - Data analytics
    - Enhanced accuracy
    - Usage tracking
    
    ---
    
    **💡 Tips for Best Results:**
    - Use good lighting
    - Show all food items clearly
    - Enable Vision AI for complex meals
    - Try different languages
    - Save data for trends
    """)
    
    st.sidebar.header("🔬 AI Services Status")
    st.sidebar.success("✅ Gemini AI (Active)")
    if GOOGLE_CLOUD_ENABLED:
        st.sidebar.success("✅ Vision AI (Available)")
        st.sidebar.success("✅ Translation (Available)")
        st.sidebar.success("✅ Text-to-Speech (Available)")
        st.sidebar.success("✅ BigQuery (Available)")
    else:
        st.sidebar.warning("⚠️ Google Cloud Services (Not Configured)")
    
    # Analysis History
    if st.session_state.analysis_history:
        st.sidebar.header("📈 Recent Analyses")
        for i, analysis in enumerate(st.session_state.analysis_history[-3:]):
            with st.sidebar.expander(f"Analysis {len(st.session_state.analysis_history)-i}"):
                st.write(f"Time: {analysis['timestamp'].strftime('%H:%M')}")
                st.write(f"Calories: {analysis['nutrition_values'].get('calories', 'N/A')}")
                st.write(f"Language: {analysis['language']}")

# Footer
st.markdown("---")
st.markdown("*Multi-AI Powered Nutrition Analysis • Gemini + Vision AI + Translation + TTS + BigQuery*")
