import streamlit as st
import requests
from PIL import Image
import io
from dotenv import load_dotenv
import os
import time

# Load environment variables
load_dotenv()
API_URL = os.getenv("FASTAPI_URL", "https://identifyobjects.onrender.com/identify-object/")

# JavaScript to detect screen width and store in session state
if "screen_width" not in st.session_state:
    st.components.v1.html("""
        <script>
            // Send screen width to Streamlit
            window.parent.postMessage({
                type: 'streamlit:setComponentValue',
                value: window.innerWidth
            }, '*');
        </script>
    """, height=0)
    st.session_state.screen_width = 800  # Default value

# Handle JavaScript message
if st.session_state.get("screen_width") is None:
    st.session_state.screen_width = 800  # Fallback for initial load

# Define mobile threshold
is_mobile = st.session_state.screen_width < 600

# Custom CSS for responsiveness, spacing, and FYNSEC-inspired design
st.markdown("""
    <style>
    /* Base styling with dark theme and teal accents */
    body {
        background: linear-gradient(135deg, #0A0A0A, #1A1A1A);
        color: #F5F5F5;
    }
    .stApp {
        background: transparent;
    }
    .stContainer > div {
        padding: 15px;
        margin-bottom: 15px;
    }
    /* Constrain image size */
    img {
        max-width: 200px;
        max-height: 200px;
        object-fit: contain;
        margin-bottom: 15px;
        border-radius: 8px;
    }
    /* Style for button */
    .stButton > button {
        margin-top: 15px;
        margin-bottom: 15px;
        width: 100%;
        background-color: #00C4B4;
        color: white;
        border: none;
        padding: 10px;
        border-radius: 8px;
        transition: background-color 0.3s;
    }
    .stButton > button:hover {
        background-color: #00A99E;
    }
    /* Stack columns vertically on mobile */
    @media (max-width: 600px) {
        .stColumn > div {
            display: block !important;
            width: 100% !important;
            margin-bottom: 20px;
        }
        .stMarkdown {
            font-size: 14px;
        }
    }
    /* Ensure text doesn't overflow */
    .stMarkdown {
        word-wrap: break-word;
        max-width: 100%;
        margin-bottom: 15px;
        color: #E5E7EB;
    }
    /* Results container with shadow */
    .results-container {
        padding: 10px;
        background-color: #1A1A1A;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    }
    /* Error styling */
    .stError {
        background-color: #7F1D1D;
        color: #FECACA;
        padding: 10px;
        border-radius: 8px;
    }
    </style>
""", unsafe_allow_html=True)

# Streamlit app title
st.title("alphaTUB - AI Vision Explorer")

# Container for uploader and language selection
with st.container():
    if is_mobile:
        # Vertical layout for mobile
        uploaded_file = st.file_uploader(
            "Upload an image",
            type=["jpg", "jpeg", "png", "heic", "heif", "webp", "gif", "bmp", "tiff", "tif"],
            label_visibility="collapsed"
        )
        language_options = ["Hindi", "Punjabi", "Khasi", "Garo", "Marathi", "Kokborok", "Spanish", "French", "German", "Bengali", "Tamil", "Telugu"]
        selected_language = st.selectbox("Select Language", language_options, label_visibility="collapsed")
    else:
        # Horizontal layout for desktop
        col1, col2 = st.columns([2, 1], gap="medium")
        with col1:
            uploaded_file = st.file_uploader(
                "Upload an image",
                type=["jpg", "jpeg", "png", "heic", "heif", "webp", "gif", "bmp", "tiff", "tif"],
                label_visibility="collapsed"
            )
        with col2:
            language_options = ["Hindi", "Punjabi", "Khasi", "Garo", "Marathi", "Kokborok", "Spanish", "French", "German", "Bengali", "Tamil", "Telugu"]
            selected_language = st.selectbox("Select Language", language_options, label_visibility="collapsed")

# Separator for clarity
st.markdown("---")

# Container for image and results
with st.container():
    if uploaded_file is not None:
        # Read the image bytes once
        uploaded_file.seek(0)  # Ensure file pointer is at the start
        image_bytes = uploaded_file.read()

        if is_mobile:
            # Vertical layout for mobile
            try:
                image = Image.open(io.BytesIO(image_bytes))
                st.image(image, caption="Uploaded Image", width=200)
            except Exception as e:
                st.error(f"Failed to display image: {str(e)}")
                st.write("Proceeding with API call despite display error.")

            # Separator for spacing
            st.markdown("---")

            # Button to trigger API call
            if st.button("Identify Object"):
                # Prepare the image and language data for the API
                files = {"image": (uploaded_file.name, image_bytes, uploaded_file.type)}
                data = {"language": selected_language}

                try:
                    # Send POST request to FastAPI endpoint with increased timeout and retry
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            response = requests.post(
                                API_URL,
                                files=files,
                                data=data,
                                headers={
                                    "Accept": "application/json"
                                },
                                timeout=30  # Increased timeout to 30 seconds
                            )
                            break
                        except requests.exceptions.ReadTimeout:
                            if attempt < max_retries - 1:
                                time.sleep(2 ** attempt)  # Exponential backoff
                                continue
                            raise
                    else:
                        raise requests.exceptions.ReadTimeout(f"Max retries ({max_retries}) reached")

                    # Check if the request was successful
                    if response.status_code == 200:
                        result = response.json()
                        st.session_state.result = result  # Store result in session state
                        
                        # Check for error in API response
                        if "error" in result:
                            st.error(f"API Error: {result['error']}")
                            st.write("Raw Output:", result.get("raw_output", "N/A"))
                            st.write("Exception:", result.get("exception", "N/A"))
                        else:
                            with st.container():
                                st.markdown("""
                                    <div class="results-container">
                                        <strong style="color: #00C4B4;">English Object Name:</strong> {0}<br>
                                        <strong style="color: #00C4B4;">Translated Object Name:</strong> {1} ({2})<br>
                                        <strong style="color: #00C4B4;">English Description:</strong> {3}<br>
                                        <strong style="color: #00C4B4;">Translated Description:</strong> {4}<br>
                                        <strong style="color: #00C4B4;">English Hint:</strong> {5}<br>
                                        <strong style="color: #00C4B4;">Translated Hint:</strong> {6}
                                    </div>
                                """.format(
                                    result["object_name_en"] or "N/A",
                                    result["object_name_translated"] or "N/A",
                                    result["translated_to"] or "N/A",
                                    result["object_description_en"] or "N/A",
                                    result["object_description_translated"] or "N/A",
                                    result["object_hint_en"] or "N/A",
                                    result["object_hint_translated"] or "N/A"
                                ), unsafe_allow_html=True)
                    else:
                        st.error(f"Error: {response.status_code} - {response.text}")
                except requests.exceptions.RequestException as e:
                    st.error(f"Failed to connect to the API: {str(e)}. Ensure the backend is running at {API_URL} and CORS is configured for the deployed app origin.")
        else:
            # Side-by-side layout for desktop
            col_image, col_results = st.columns([1, 2], gap="large")
            with col_image:
                try:
                    image = Image.open(io.BytesIO(image_bytes))
                    st.image(image, caption="Uploaded Image", width=200)
                except Exception as e:
                    st.error(f"Failed to display image: {str(e)}")
                    st.write("Proceeding with API call despite display error.")

            with col_results:
                # Button to trigger API call
                if st.button("Identify Object"):
                    # Prepare the image and language data for the API
                    files = {"image": (uploaded_file.name, image_bytes, uploaded_file.type)}
                    data = {"language": selected_language}

                    try:
                        # Send POST request to FastAPI endpoint with increased timeout and retry
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                response = requests.post(
                                    API_URL,
                                    files=files,
                                    data=data,
                                    headers={
                                        "Accept": "application/json"
                                    },
                                    timeout=30  # Increased timeout to 30 seconds
                                )
                                break
                            except requests.exceptions.ReadTimeout:
                                if attempt < max_retries - 1:
                                    time.sleep(2 ** attempt)  # Exponential backoff
                                    continue
                                raise
                        else:
                            raise requests.exceptions.ReadTimeout(f"Max retries ({max_retries}) reached")

                        # Check if the request was successful
                        if response.status_code == 200:
                            result = response.json()
                            st.session_state.result = result  # Store result in session state
                            
                            # Check for error in API response
                            if "error" in result:
                                st.error(f"API Error: {result['error']}")
                                st.write("Raw Output:", result.get("raw_output", "N/A"))
                                st.write("Exception:", result.get("exception", "N/A"))
                            else:
                                with st.container():
                                    st.markdown("""
                                        <div class="results-container">
                                            <strong style="color: #00C4B4;">English Object Name:</strong> {0}<br>
                                            <strong style="color: #00C4B4;">Translated Object Name:</strong> {1} ({2})<br>
                                            <strong style="color: #00C4B4;">English Description:</strong> {3}<br>
                                            <strong style="color: #00C4B4;">Translated Description:</strong> {4}<br>
                                            <strong style="color: #00C4B4;">English Hint:</strong> {5}<br>
                                            <strong style="color: #00C4B4;">Translated Hint:</strong> {6}
                                        </div>
                                    """.format(
                                        result["object_name_en"] or "N/A",
                                        result["object_name_translated"] or "N/A",
                                        result["translated_to"] or "N/A",
                                        result["object_description_en"] or "N/A",
                                        result["object_description_translated"] or "N/A",
                                        result["object_hint_en"] or "N/A",
                                        result["object_hint_translated"] or "N/A"
                                    ), unsafe_allow_html=True)
                        else:
                            st.error(f"Error: {response.status_code} - {response.text}")
                    except requests.exceptions.RequestException as e:
                        st.error(f"Failed to connect to the API: {str(e)}. Ensure the backend is running at {API_URL} and CORS is configured for the deployed app origin.")
                else:
                    st.write("Click 'Identify Object' to see results.")
    else:
        # Placeholder when no image is uploaded
        if is_mobile:
            st.write("No image uploaded.")
            st.info("Please upload an image to identify.")
        else:
            col_image, col_results = st.columns([1, 2], gap="large")
            with col_image:
                st.write("No image uploaded.")
            with col_results:
                st.info("Please upload an image to identify.")

# Separator for clarity
st.markdown("---")