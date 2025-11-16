#streamlit app where customer will answer some questions about their food preferences and then will be shown some tiles of recipes that they can order based on their preferences
#the questions will be: what diet type do they prefer, what allergies do they have, what is their budget, what is the prefered cuisine
#the recipes will be shown in a grid of 3x3 tiles, each tile will have the recipe name, the recipe image, the recipe ingredients, the recipe instructions.

import streamlit as st
import pandas as pd
import requests
import json
import yaml
import os
from datetime import datetime
from dotenv import load_dotenv
import boto3
from botocore.exceptions import ClientError

# Load environment variables from .env file
# Try multiple paths to find .env file
script_dir = os.path.dirname(os.path.abspath(__file__))
env_paths = [
    os.path.join(script_dir, '.env'),  # Same directory as script
    '.env',  # Current working directory
    os.path.join(os.getcwd(), '.env')  # Absolute path from cwd
]

env_loaded = False
for env_path in env_paths:
    if os.path.exists(env_path):
        result = load_dotenv(dotenv_path=env_path, override=True)
        if result:
            env_loaded = True
            break

# Fallback: try loading without explicit path (uses default .env search)
if not env_loaded:
    load_dotenv(override=True)

# Initialize AWS Bedrock client
@st.cache_resource
def get_bedrock_client():
    """Initialize and return Bedrock client"""
    try:
        # Get credentials from environment variables (check both uppercase and lowercase)
        aws_access_key = os.getenv('AWS_ACCESS_KEY_ID') or os.getenv('aws_access_key_id')
        aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY') or os.getenv('aws_secret_access_key')
        aws_session_token = os.getenv('AWS_SESSION_TOKEN') or os.getenv('aws_session_token')

        # Debug: Check if .env file exists and credentials are loaded
        env_file_exists = os.path.exists('.env') or os.path.exists(os.path.join(os.path.dirname(__file__), '.env'))
        
        # Check if credentials are loaded
        if not aws_access_key or not aws_secret_key:
            error_msg = "⚠️ AWS credentials not found.\n\n"
            if not env_file_exists:
                error_msg += "❌ .env file not found in the project directory.\n"
            else:
                error_msg += "✅ .env file found, but credentials not loaded.\n"
            error_msg += "\nPlease ensure your .env file contains:\n"
            error_msg += "AWS_ACCESS_KEY_ID=your_access_key\n"
            error_msg += "AWS_SECRET_ACCESS_KEY=your_secret_key\n"
            if aws_session_token:
                error_msg += "AWS_SESSION_TOKEN=your_session_token (optional)\n"
            st.error(error_msg)
            return None
        
        # Build client config - session token is optional (only needed for temporary credentials)
        client_config = {
            'service_name': 'bedrock-runtime',
            'region_name': 'eu-west-1',
            'aws_access_key_id': aws_access_key,
            'aws_secret_access_key': aws_secret_key
        }
        
        # Add session token only if provided (for temporary credentials)
        if aws_session_token:
            client_config['aws_session_token'] = aws_session_token
        
        # Initialize the client
        client = boto3.client(**client_config)
        
        return client
    except Exception as e:
        st.error(f"❌ Error initializing Bedrock client: {str(e)}")
        st.info("💡 Troubleshooting tips:\n"
                "- Verify AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are correct in .env\n"
                "- Check that your AWS account has Bedrock access enabled\n"
                "- Ensure your IAM user/role has bedrock:InvokeModel permission\n"
                "- Verify the region 'eu-west-1' is correct for your setup")
        return None

# Load context from YAML file
@st.cache_data
def load_context():
    """Load context from context.yml"""
    try:
        with open('context.yml', 'r') as f:
            context_data = yaml.safe_load(f)
            return context_data.get('context', '')
    except Exception as e:
        st.error(f"Error loading context.yml: {str(e)}")
        return ""

# Function to test Bedrock connection
def test_bedrock_connection():
    """Test if Bedrock connection works"""
    try:
        bedrock_client = get_bedrock_client()
        if not bedrock_client:
            return False, "Bedrock client not initialized"
        
        # Try a simple test call with minimal payload
        test_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "Hi"}],
            "system": "You are a helpful assistant."
        }
        
        response = bedrock_client.invoke_model(
            modelId="anthropic.claude-3-sonnet-20240229-v1:0",
            body=json.dumps(test_body)
        )
        return True, "Connection successful"
    except ClientError as e:
        return False, f"AWS Error: {e.response['Error'].get('Message', str(e))}"
    except Exception as e:
        return False, f"Connection Error: {str(e)}"

# Function to submit preferences via API
def submit_preferences_api(email, diet_type, allergies, budget, cuisine):
    """Actually submit preferences to the API"""
    try:
        API_BASE_URL = "http://localhost:8080"
        api_url = f"{API_BASE_URL}/api/preferences"
        
        payload = {
            "email": email,
            "diet_type": diet_type,
            "allergies": allergies if allergies else [],
            "budget": float(budget),
            "cuisine": cuisine,
            "loaded_at": datetime.now().isoformat()
        }
        
        response = requests.post(api_url, json=payload, timeout=10)
        response.raise_for_status()
        return True, f"✅ Preferences submitted successfully! Status: {response.status_code}"
    except requests.exceptions.ConnectionError:
        return False, "❌ Error: Could not connect to the API. Please ensure the Go service is running on http://localhost:8080"
    except requests.exceptions.Timeout:
        return False, "❌ Error: API request timed out. Please try again."
    except requests.exceptions.HTTPError as e:
        return False, f"❌ Error: API returned status {response.status_code}. {response.text}"
    except Exception as e:
        return False, f"❌ Error submitting preferences: {str(e)}"

# Function to call Bedrock Claude model
def get_ai_response(user_message, conversation_history, user_email=None):
    """Get AI response from Bedrock Claude model"""
    try:
        bedrock_client = get_bedrock_client()
        if not bedrock_client:
            return "❌ Error: Could not initialize AWS Bedrock client. Please check your credentials in the .env file."
        
        # Load context
        context = load_context()
        
        # Add email information to context if available
        if user_email:
            email_context = f"\n\nIMPORTANT: The user's email address is {user_email}. Use this email when submitting preferences via the API."
            context = context + email_context if context else email_context
        
        # Update context to tell AI it cannot make API calls directly
        api_instruction = "\n\nCRITICAL: You CANNOT make HTTP API calls directly. When you need to submit preferences, you must respond with a special format: 'SUBMIT_PREFERENCES: diet_type=X, allergies=[Y], budget=Z, cuisine=W'. The system will then make the actual API call and inform you of the result."
        context = context + api_instruction if context else api_instruction
        
        # Build the prompt with context and conversation history
        system_prompt = context if context else "You are a helpful assistant for a food recipe recommender system."
        
        # Build messages for Claude 3 Sonnet
        # Claude requires: 
        # 1. First message must be "user"
        # 2. Messages must alternate between "user" and "assistant"
        messages = []
        
        # Process conversation history and ensure proper alternation
        last_role = None
        for msg in conversation_history[-10:]:  # Keep last 10 messages for context
            current_role = msg.get('role')
            content = msg.get('content', '').strip()
            
            if not content:  # Skip empty messages
                continue
                
            # Map 'bot' to 'assistant' and 'user' to 'user'
            if current_role == 'bot':
                claude_role = 'assistant'
            elif current_role == 'user':
                claude_role = 'user'
            else:
                continue  # Skip unknown roles
            
            # Ensure roles alternate - if same role as last, combine with previous message
            if last_role == claude_role:
                # Combine with previous message
                if messages:
                    messages[-1]["content"] += "\n\n" + content
                else:
                    # First message - must be "user", skip if it's "assistant"
                    if claude_role == 'user':
                        messages.append({
                            "role": claude_role,
                            "content": content
                        })
                        last_role = claude_role
                    # If first message is assistant, skip it (will start with user message below)
            else:
                # Different role, add as new message
                # But ensure first message is always "user"
                if not messages and claude_role == 'assistant':
                    # Skip assistant messages at the start, we'll start with user
                    continue
                messages.append({
                    "role": claude_role,
                    "content": content
                })
                last_role = claude_role
        
        # Add current user message
        # Ensure first message is always "user"
        if not messages:
            # No history, start with user message
            messages.append({
                "role": "user",
                "content": user_message
            })
        elif messages[-1]["role"] == "user":
            # Last message was user, combine
            messages[-1]["content"] += "\n\n" + user_message
        else:
            # Last message was assistant, add new user message
            messages.append({
                "role": "user",
                "content": user_message
            })
        
        # Final validation: ensure first message is "user"
        if messages and messages[0]["role"] != "user":
            # If first message is not user, prepend a user message or remove assistant messages at start
            # Remove any leading assistant messages
            while messages and messages[0]["role"] == "assistant":
                messages.pop(0)
            # If we removed everything or still have issue, start fresh with user message
            if not messages:
                messages.append({
                    "role": "user",
                    "content": user_message
                })
        
        # Prepare the request body for Claude 3 Sonnet
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": messages,
            "system": system_prompt
        }
        
        # Call Bedrock
        try:
            response = bedrock_client.invoke_model(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                body=json.dumps(body)
            )
        except Exception as invoke_error:
            # Provide more specific error information
            error_str = str(invoke_error)
            if "InvalidSignatureException" in error_str or "Signature" in error_str:
                return "❌ Authentication failed. Please verify your AWS credentials (AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY) are correct in the .env file."
            elif "EndpointConnectionError" in error_str or "Could not connect" in error_str:
                return f"❌ Could not connect to AWS Bedrock endpoint in eu-west-1. Please check:\n- Your internet connection\n- AWS service status\n- Region configuration\n\nError: {error_str}"
            else:
                return f"❌ Error calling Bedrock API: {error_str}"
        
        # Parse response - read the body stream
        response_body = json.loads(response.get('body').read())
        
        # Extract text from Claude response
        if 'content' in response_body and len(response_body['content']) > 0:
            ai_response = response_body['content'][0].get('text', '')
            
            # Check if AI wants to submit preferences
            if 'SUBMIT_PREFERENCES:' in ai_response and user_email:
                # Parse the preference submission
                try:
                    # Extract the preferences from the response
                    import re
                    match = re.search(r'SUBMIT_PREFERENCES:\s*diet_type=([^,]+),\s*allergies=\[([^\]]*)\],\s*budget=([^,]+),\s*cuisine=([^\n]+)', ai_response)
                    if match:
                        diet_type = match.group(1).strip()
                        allergies_str = match.group(2).strip()
                        budget = match.group(3).strip()
                        cuisine = match.group(4).strip()
                        
                        # Parse allergies
                        allergies = []
                        if allergies_str:
                            allergies = [a.strip().strip('"\'') for a in allergies_str.split(',') if a.strip()]
                        
                        # Actually submit the preferences
                        success, result_msg = submit_preferences_api(user_email, diet_type, allergies, budget, cuisine)
                        
                        # Return the result to the user
                        if success:
                            return f"{ai_response}\n\n{result_msg}"
                        else:
                            return f"{ai_response}\n\n{result_msg}"
                    else:
                        # Couldn't parse, return original response
                        return ai_response
                except Exception as e:
                    return f"{ai_response}\n\n❌ Error parsing preference submission: {str(e)}"
            
            return ai_response
        else:
            return "I apologize, but I couldn't generate a response. Please try again."
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error'].get('Message', str(e))
        
        if error_code == 'AccessDeniedException':
            return f"❌ Access denied. Please check:\n- Your AWS credentials are correct\n- Your IAM user/role has 'bedrock:InvokeModel' permission\n- Bedrock is enabled in your AWS account for region eu-west-1\n\nError details: {error_message}"
        elif error_code == 'ValidationException':
            return f"❌ Validation error: {error_message}\n\nPlease check the request format and model ID."
        elif error_code == 'ThrottlingException':
            return "⏳ Rate limit exceeded. Please wait a moment and try again."
        else:
            return f"❌ AWS Bedrock error ({error_code}): {error_message}"
    except Exception as e:
        error_msg = str(e)
        if "Could not connect" in error_msg or "Connection" in error_msg:
            return f"❌ Connection error: Could not connect to AWS Bedrock.\n\nPlease verify:\n- Your internet connection\n- AWS service status\n- Region 'eu-west-1' is correct\n\nError: {error_msg}"
        return f"❌ Error: {error_msg}"

# Custom CSS to increase page width
st.markdown("""
<style>
    /* Increase overall page width */
    .main .block-container {
        max-width: 95% !important;
        padding-left: 5rem !important;
        padding-right: 5rem !important;
    }
    
    /* Make columns wider */
    section[data-testid="stSidebar"] {
        display: none;
    }
    
    /* Ensure full width utilization */
    .stApp {
        max-width: 100% !important;
    }
</style>
""", unsafe_allow_html=True)

st.title("Food Recipe Recommender")

st.write("Answer a few questions to get personalized recipe recommendations")

# API Configuration
API_BASE_URL ="http://localhost:8080"

# Initialize session state for chat
if 'chat_open' not in st.session_state:
    st.session_state.chat_open = False
if 'chat_messages' not in st.session_state:
    st.session_state.chat_messages = []

# Create two columns: left for preferences, right for chatbot
pref_col, chat_col = st.columns([1, 1.5])

with pref_col:
    st.markdown("### 📝 Your Preferences")
    
    #question 0: email and it can't be empty
    # Initialize email in session state
    if 'user_email' not in st.session_state:
        st.session_state.user_email = ""

    email = st.text_input("What is your email address?", value=st.session_state.user_email)
    if email == "" or '@' not in email:
        st.error("Email is required")
        st.stop()
    else:
        # Store email in session state
        st.session_state.user_email = email

    # Question 1: Diet Type
    diet_type = st.selectbox("What diet type do you prefer?", ["Vegetarian", "Vegan", "Non-Vegetarian", "Keto", "Paleo", "Gluten-Free", "Dairy-Free"])

    # Question 2: Allergies
    allergies = st.multiselect("Do you have any allergies?", ["Gluten", "Dairy", "Eggs", "Soy", "Nuts", "Seafood"], default=None)

    # Question 3: Budget
    budget = st.selectbox("What is your budget for a meal?", [10, 20, 25, 50], index=1)

    # Question 4: Cuisine
    cuisine = st.selectbox("What is your preferred cuisine?", ["Italian", "Mexican", "Japanese", "Indian", "American", "Vietnamese", "No Preference"])

    #On submit, call GO POST API
    if st.button("Submit Preferences"):
        try:
            # Prepare request payload
            payload = {
                "email": email,
                "diet_type": diet_type,
                "allergies": allergies,
                "budget": budget,
                "cuisine": cuisine,
                "loaded_at": datetime.now().isoformat()
            }
            # Make API call to Golang microservice
            api_url = f"{API_BASE_URL}/api/preferences"
            with st.spinner("Submitting preferences to microservice..."):
                response = requests.post(api_url, json=payload, timeout=10)
                response.raise_for_status()
                st.success("Preferences submitted successfully")
        except requests.exceptions.RequestException as e:
            st.error(f"Error submitting preferences: {str(e)}")
            st.info("Please check that the Golang microservice is running and the API URL is correct.")
        except json.JSONDecodeError as e:
            st.error(f"Error parsing API response: {str(e)}")
        except Exception as e:
            st.error(f"Unexpected error: {str(e)}")

with chat_col:
    st.markdown("### 🤖 Chat Assistant")
    
    # Chat interface CSS
    st.markdown("""
    <style>
        /* Inline chat container - ChatGPT style */
        .chat-container-inline {
            width: 100%;
            background-color: white;
            border-radius: 10px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            border: 1px solid #e0e0e0;
            display: flex;
            flex-direction: column;
            min-height: 500px;
            max-height: 600px;
        }
        .chat-messages-inline {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            background-color: #ffffff;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }
        .chat-messages-inline::-webkit-scrollbar {
            width: 8px;
        }
        .chat-messages-inline::-webkit-scrollbar-track {
            background: #f1f1f1;
        }
        .chat-messages-inline::-webkit-scrollbar-thumb {
            background: #888;
            border-radius: 4px;
        }
        .chat-messages-inline::-webkit-scrollbar-thumb:hover {
            background: #555;
        }
        .chat-message-inline {
            padding: 16px 20px;
            border-radius: 8px;
            word-wrap: break-word;
            line-height: 1.6;
            animation: fadeIn 0.3s ease-in;
            max-width: 85%;
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .chat-message-inline.user {
            background-color: #1f77b4;
            color: white;
            margin-left: auto;
            align-self: flex-end;
        }
        .chat-message-inline.bot {
            background-color: #f0f2f6;
            color: #262730;
            margin-right: auto;
            align-self: flex-start;
        }
        .chat-input-area-inline {
            padding: 20px;
            background-color: white;
            border-top: 1px solid #e0e0e0;
            border-radius: 0 0 10px 10px;
        }
    </style>
    """, unsafe_allow_html=True)
    
    # Build messages HTML
    messages_html = ""
    if st.session_state.chat_messages:
        import html
        for msg in st.session_state.chat_messages:
            role = msg.get('role', 'bot')
            content = html.escape(msg.get('content', ''))
            messages_html += f'<div class="chat-message-inline {role}">{content}</div>'
    else:
        messages_html = '<div class="chat-message-inline bot">Hello! 👋 How can I help you with recipe recommendations today?</div>'
    
    # Render chat container with messages
    st.markdown(f"""
    <div class="chat-container-inline">
        <div class="chat-messages-inline" id="chat-messages-inline-div">
            {messages_html}
        </div>
    </div>
    <script>
        // Auto-scroll to bottom when messages are added
        setTimeout(function() {{
            const messagesDiv = document.getElementById('chat-messages-inline-div');
            if (messagesDiv) {{
                messagesDiv.scrollTop = messagesDiv.scrollHeight;
            }}
        }}, 100);
    </script>
    """, unsafe_allow_html=True)
    
    # Chat input form using Streamlit
    with st.form(key="chat_form", clear_on_submit=True):
        input_col1, input_col2 = st.columns([8, 2])
        
        with input_col1:
            user_input = st.text_input(
                "", 
                key="chat_input", 
                placeholder="Type your message here...", 
                label_visibility="collapsed"
            )
        
        with input_col2:
            # Send button with icon - wider button
            send_button = st.form_submit_button("➤ Send", use_container_width=True, type="primary", help="Send message")
    
    # Handle message sending
    if send_button and user_input:
        # Add user message
        st.session_state.chat_messages.append({
            'role': 'user',
            'content': user_input
        })
        
        # Get AI response from Bedrock (pass user email if available)
        user_email = st.session_state.get('user_email', None)
        with st.spinner("Thinking..."):
            bot_response = get_ai_response(user_input, st.session_state.chat_messages, user_email=user_email)
        
        # Add bot response
        st.session_state.chat_messages.append({
            'role': 'bot',
            'content': bot_response
        })
        st.rerun()

# Initialize recipe database
recipe_database = pd.DataFrame()

# Initialize session state for pagination
if 'page' not in st.session_state:
    st.session_state.page = 0
if 'show_recipes' not in st.session_state:
    st.session_state.show_recipes = False

# Recipes per page (3x3 grid = 9 recipes)
recipes_per_page = 9

#add button to show recipes
if st.button("Show Recipes"):
    try:
        # Get user email from session state
        user_email = st.session_state.get('user_email', email)
        
        # Make API call to Golang microservice
        # Backend will use the email to fetch preferences from Redis and filter recipes accordingly
        api_url = f"{API_BASE_URL}/api/recipes"
        params = {"email": user_email} if user_email else {}
        
        with st.spinner("Fetching recipes from microservice..."):
            response = requests.get(api_url, params=params, timeout=10)
            response.raise_for_status()
            
            # Parse JSON response
            recipes_data = response.json()
            
            # Convert response to DataFrame
            # The Go service returns {"recipes": [...], "count": ...}
            if isinstance(recipes_data, dict) and 'recipes' in recipes_data:
                recipe_database = pd.DataFrame(recipes_data['recipes'])
            elif isinstance(recipes_data, list):
                recipe_database = pd.DataFrame(recipes_data)
            elif isinstance(recipes_data, dict) and 'data' in recipes_data:
                recipe_database = pd.DataFrame(recipes_data['data'])
            else:
                recipe_database = pd.DataFrame(recipes_data)
            
            # Store in session state
            st.session_state.recipe_database = recipe_database
            st.session_state.page = 0  # Reset to first page when showing recipes
            st.session_state.show_recipes = True
            st.success(f"Successfully loaded {len(recipe_database)} recipes!")
            
    except requests.exceptions.RequestException as e:
        st.error(f"Error connecting to API: {str(e)}")
        st.info("Please check that the Golang microservice is running and the API URL is correct.")
        st.session_state.show_recipes = False
    except json.JSONDecodeError as e:
        st.error(f"Error parsing API response: {str(e)}")
        st.session_state.show_recipes = False
    except Exception as e:
        st.error(f"Unexpected error: {str(e)}")
        st.session_state.show_recipes = False

# Get recipe database from session state if available
if 'recipe_database' in st.session_state and not st.session_state.recipe_database.empty:
    recipe_database = st.session_state.recipe_database
else:
    recipe_database = pd.DataFrame()

total_recipes = len(recipe_database) if not recipe_database.empty else 0
total_pages = (total_recipes + recipes_per_page - 1) // recipes_per_page if total_recipes > 0 else 0

# Display recipes if button was clicked and data is available
if st.session_state.show_recipes and not recipe_database.empty:
    # Calculate start and end indices for current page
    start_idx = st.session_state.page * recipes_per_page
    end_idx = min(start_idx + recipes_per_page, total_recipes)
    
    # Get recipes for current page
    page_recipes = recipe_database.iloc[start_idx:end_idx]
    
    # Calculate number of rows needed (3 columns per row)
    num_rows = (len(page_recipes) + 2) // 3
    
    # Display recipes in 3x3 grid
    for i in range(num_rows):
        cols = st.columns(3)
        for j in range(3):
            recipe_idx = i * 3 + j
            if recipe_idx < len(page_recipes):
                recipe = page_recipes.iloc[recipe_idx]
                with cols[j]:
                    # Convert price to euros (assuming USD to EUR conversion rate ~0.92)
                    price_usd = float(recipe["price"])
                    price_eur = price_usd * 0.92
                    time_to_cook = int(recipe["time_to_cook"])
                    is_express = time_to_cook <= 15
                    express_text = " - EXPRESS" if is_express else ""
                    # Wrap entire recipe in a styled box
                    st.markdown(
                        f"""
                        <div style="background-color: #ffffff; border: 1px solid #e0e0e0; border-radius: 10px; padding: 15px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                            <div style="text-align: center;">
                                <img src="{recipe["image_url"]}" style="width: 200px; height: 150px; object-fit: cover; border-radius: 5px; margin-bottom: 10px;">
                                <h3 style="margin: 10px 0; color: #262730; font-size: 1.1em;">{recipe["name"]}</h3>
                                <div style="background-color: #f0f2f6; padding: 10px; border-radius: 5px; margin-top: 10px;">
                                    <p style="margin: 0; font-size: 0.9em; color: #262730;">{recipe["description"]}</p>
                                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 10px;">
                                        <span style="font-size: 0.9em; color: #262730;">
                                            ⏱️ {time_to_cook} mins{express_text}
                                        </span>
                                        <span style="font-size: 0.95em; color: #262730; font-weight: bold;">
                                            ${price_usd:.0f} / €{price_eur:.2f}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
    
    # Pagination controls
    if total_pages > 1:
        col1, col2, col3, col4, col5 = st.columns([1, 1, 2, 1, 1])
        
        with col1:
            if st.button("◀ Previous", disabled=(st.session_state.page == 0)):
                st.session_state.page -= 1
                st.rerun()
        
        with col3:
            st.write(f"Page {st.session_state.page + 1} of {total_pages}")
        
        with col5:
            if st.button("Next ▶", disabled=(st.session_state.page >= total_pages - 1)):
                st.session_state.page += 1
                st.rerun()
