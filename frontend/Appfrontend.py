import streamlit as st
import requests
from PIL import Image
import io
import streamlit.components.v1 as components
from dotenv import load_dotenv
import os
import base64

# Load environment variables
load_dotenv()
API_URL = os.getenv("FASTAPI_URL", "http://localhost:8000/identify-object/")

# JavaScript to detect screen width and store in session state
if "screen_width" not in st.session_state:
    components.html("""
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

# Custom CSS for responsiveness, spacing, preventing overlap, and drag-and-drop styling
st.markdown("""
    <style>
    /* Add padding and margins to containers */
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
    }
    /* Style for button */
    .stButton > button {
        margin-top: 15px;
        margin-bottom: 15px;
        width: 100%;
    }
    /* Stack columns vertically on mobile */
    @media (max-width: 600px) {
        .stColumn > div {
            display: block !important;
            width: 100% !important;
            margin-bottom: 20px;
        }
        .stMarkdown {
            font-size: 14px; /* Smaller font for mobile */
        }
    }
    /* Ensure text doesn't overflow */
    .stMarkdown {
        word-wrap: break-word;
        max-width: 100%;
        margin-bottom: 15px;
    }
    /* Add spacing around results */
    .results-container {
        padding: 10px;
    }
    /* Drag and drop styling */
    .drag-drop-zone {
        border: 2px dashed #ccc;
        padding: 20px;
        text-align: center;
        background-color: #f9f9f9;
        cursor: pointer;
        margin-bottom: 15px;
    }
    .drag-drop-zone:hover {
        background-color: #e0e0e0;
    }
    .drag-drop-zone.dragover {
        border-color: #007bff;
        background-color: #e6f0ff;
    }
    </style>
""", unsafe_allow_html=True)

# Streamlit app title
st.title("alphaTUB App - TUBShots with AI")

# Custom drag-and-drop component
def drag_drop_uploader():
    components.html("""
        <div id="drag-drop-zone" class="drag-drop-zone">
            Drag and drop an image here or click to upload<br>
            <input type="file" id="fileInput" accept=".jpg,.jpeg,.png,.heic,.heif,.webp,.gif,.bmp,.tiff,.tif" style="display: none;">
            <small>Supported formats: jpg, jpeg, png, heic, heif, webp, gif, bmp, tiff, tif</small>
        </div>
        <script>
            const dragDropZone = document.getElementById('drag-drop-zone');
            const fileInput = document.getElementById('fileInput');

            // Open file input on click
            dragDropZone.addEventListener('click', () => {
                fileInput.click();
            });

            // Handle drag events
            dragDropZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dragDropZone.classList.add('dragover');
            });

            dragDropZone.addEventListener('dragleave', () => {
                dragDropZone.classList.remove('dragover');
            });

            // Handle file drop
            dragDropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                dragDropZone.classList.remove('dragover');
                const files = e.dataTransfer.files;
                if (files.length > 0) {
                    handleFile(files[0]);
                }
            });

            // Handle file input change
            fileInput.addEventListener('change', () => {
                if (fileInput.files.length > 0) {
                    handleFile(fileInput.files[0]);
                }
            });

            // Function to handle file and send to Streamlit
            function handleFile(file) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    const fileData = {
                        name: file.name,
                        type: file.type,
                        content: e.target.result.split(',')[1] // Base64 content without data URL prefix
                    };
                    window.parent.postMessage({
                        type: 'streamlit:setComponentValue',
                        value: fileData
                    }, '*');
                };
                reader.readAsDataURL(file);
            }
        </script>
    """, height=100)

    # Check if a file was uploaded via drag-and-drop
    if "drag_drop_file" in st.session_state:
        file_data = st.session_state.get("drag_drop_file")
        if file_data and all(k in file_data for k in ["name", "type", "content"]):
            # Convert base64 content back to bytes
            file_bytes = base64.b64decode(file_data["content"])
            return type("UploadedFile", (), {
                "name": file_data["name"],
                "type": file_data["type"],
                "read": lambda self: file_bytes,
                "seek": lambda self, offset: None  # Dummy seek for compatibility
            })()
    return None

# Container for uploader and language selection
with st.container():
    if is_mobile:
        # Vertical layout for mobile
        uploaded_file = drag_drop_uploader()
        if not uploaded_file:
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
            uploaded_file = drag_drop_uploader()
            if not uploaded_file:
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
                    # Send POST request to FastAPI endpoint
                    response = requests.post(
                        API_URL,
                        files=files,
                        data=data
                    )

                    # Check if the request was successful
                    if response.status_code == 200:
                        result = response.json()
                        
                        # Check for error in API response
                        if "error" in result:
                            st.error(f"API Error: {result['error']}")
                            st.write("Raw Output:", result.get("raw_output", "N/A"))
                            st.write("Exception:", result.get("exception", "N/A"))
                        else:
                            with st.container():
                                st.markdown("""
                                    **Object Name (English):** {0}  
                                    **Object Name (Translated):** {1} ({2})  
                                    **Description (English):** {3}  
                                    **Description (Translated):** {4}  
                                    **Hint (English):** {5}  
                                    **Hint (Translated):** {6}
                                """.format(
                                    result["object_name_en"],
                                    result["object_name_translated"],
                                    result["translated_to"],
                                    result["object_description_en"],
                                    result["object_description_translated"],
                                    result["object_hint_en"],
                                    result["object_hint_translated"]
                                ))
                    else:
                        st.error(f"Error: {response.status_code} - {response.text}")
                except requests.exceptions.RequestException as e:
                    st.error(f"Failed to connect to the API: {str(e)}")
            else:
                st.write("Click 'Identify Object' to see results.")
        else:
            # Side-by-side layout for desktop
            col_image, col_results = st.columns([1, 3], gap="large")
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
                        # Send POST request to FastAPI endpoint
                        response = requests.post(
                            API_URL,
                            files=files,
                            data=data
                        )

                        # Check if the request was successful
                        if response.status_code == 200:
                            result = response.json()
                            
                            # Check for error in API response
                            if "error" in result:
                                st.error(f"API Error: {result['error']}")
                                st.write("Raw Output:", result.get("raw_output", "N/A"))
                                st.write("Exception:", result.get("exception", "N/A"))
                            else:
                                with st.container():
                                    st.markdown("""
                                        **Object Name (English):** {0}  
                                        **Object Name (Translated):** {1} ({2})  
                                        **Description (English):** {3}  
                                        **Description (Translated):** {4}  
                                        **Hint (English):** {5}  
                                        **Hint (Translated):** {6}
                                    """.format(
                                        result["object_name_en"],
                                        result["object_name_translated"],
                                        result["translated_to"],
                                        result["object_description_en"],
                                        result["object_description_translated"],
                                        result["object_hint_en"],
                                        result["object_hint_translated"]
                                    ))
                        else:
                            st.error(f"Error: {response.status_code} - {response.text}")
                    except requests.exceptions.RequestException as e:
                        st.error(f"Failed to connect to the API: {str(e)}")
                else:
                    st.write("Click 'Identify Object' to see results.")
    else:
        # Placeholder when no image is uploaded
        if is_mobile:
            st.write("No image uploaded.")
            st.info("Please upload an image to identify.")
        else:
            col_image, col_results = st.columns([1, 3], gap="large")
            with col_image:
                st.write("No image uploaded.")
            with col_results:
                st.info("Please upload an image to identify.")

# Separator for clarity
st.markdown("---")