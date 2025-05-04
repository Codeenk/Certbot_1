import os
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import google.generativeai as genai
import logging
import markdown2
import re
from flask import Flask, request

# Set up Flask app
app = Flask(__name__)

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Check for required environment variables
if not os.getenv('TELEGRAM_BOT_TOKEN'):
    raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables")
if not os.getenv('GEMINI_API_KEY'):
    raise ValueError("GEMINI_API_KEY not found in environment variables")

# Configure Gemini AI
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.0-flash')

# Conversation states
TOPIC, LEARNING, ASSESSMENT, CERTIFICATION = range(4)

# User session storage
user_data = {}

def format_response(text):
    # Clean any existing HTML tags first
    text = re.sub(r'<[^>]+>', '', text)
    
    # Format code blocks with Telegram-compatible tags
    text = re.sub(r'```(\w+)?\n(.*?)\n```', r'<pre><code>\2</code></pre>', text, flags=re.DOTALL)
    
    # Convert markdown to plain text formatting
    text = re.sub(r'#+ +(.*?)(?:\n|$)', r'\1\n', text)
    text = re.sub(r'^\s*[*-] +(.*)$', r'• \1', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    
    # Clean up video links
    text = re.sub(r'Link:\s*(https?://[^\s]+)', r'🔗 \1', text)
    
    # Ensure proper spacing
    text = text.replace('\n\n', '\n')  # Remove extra newlines
    text = text.replace('</code></pre>', '</code></pre>\n')  # Add newline after code blocks
    
    return text

class CourseModule:
    def __init__(self, topic):
        self.topic = topic
        self.current_module = 1
        self.completed_modules = {}
        self.module_content = {}
        self.modules = self._generate_modules()
        
    def get_module_video_link(self, module_number):
        if module_number in self.module_content:
            content = self.module_content[module_number]
            # Extract video link using regex
            video_match = re.search(r'🔗\s*(https?://[^\s]+)', content)
            if video_match:
                return video_match.group(1)
        return None
    
    def _store_module_content(self, module_number, content):
        self.module_content[module_number] = content
        
    def _generate_modules(self):
        try:
            prompt = f"""Generate a structured learning path for {self.topic} with 5 modules.
            For each module provide:
            1. A clear title
            2. A very short description of what will be learned
            3. One specific YouTube video link from a reputable educational channel
            4. design the course such that it gets fully completed and makes the user master in that course.
            
            Format as follows:

            📚 Module 1: [NAME]
            
            📝 Description:
            [2-3 sentences about what will be learned]
           
            🎬 Educational Video:
            • Title: [specific video title]
              Link: https://youtube.com/... (use real educational video links)
              Duration: [approximate duration]

            [Repeat for all 5 modules]"""

            response = model.generate_content(prompt)
            
            # Extract and store individual module content
            modules_content = re.split(r'📚\s*Module\s+\d+:', response.text)
            if len(modules_content) > 1:
                modules_content = modules_content[1:]  # Skip first empty split
                for i, content in enumerate(modules_content, 1):
                    self._store_module_content(i, content.strip())
            
            formatted_response = format_response(response.text)
            if len(formatted_response) > 4000:
                formatted_response = formatted_response[:3997] + "..."
                
            return formatted_response
        except Exception as e:
            logger.error(f"Error generating modules: {str(e)}")
            return "Error generating learning path. Please try again with a different topic."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to the Learning Bot! 🎓\n"
        "What topic would you like to learn? (e.g., Machine Learning, Python, Web Development)"
    )
    return TOPIC

async def set_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    topic = update.message.text
    user_data[user_id] = CourseModule(topic)
    
    modules = user_data[user_id].modules
    
    # Split long messages if needed
    if len(modules) > 4000:
        chunks = [modules[i:i+4000] for i in range(0, len(modules), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(
            f"Great choice! Here's your learning path for {topic}:\n\n"
            f"{modules}\n\n"
            "When you complete a module, type 'completed module X'\n"
            "For example: 'completed module 1'",
            parse_mode=ParseMode.HTML
        )
    return LEARNING

async def handle_learning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message_text = update.message.text.lower()
    
    # Check if this is a course completion request
    if message_text == 'course completed':
        if len(user_data[user_id].completed_modules) == 5:  # All modules must be completed
            return await handle_certification(update, context)
        else:
            await update.message.reply_text(
                "⚠️ You need to complete all 5 modules before you can get the course completion certificate.\n"
                f"You have completed {len(user_data[user_id].completed_modules)} out of 5 modules."
            )
            return LEARNING
            
    # Check if user is providing a video link
    if 'video_pending' in context.user_data:
        if message_text.startswith('http'):
            module_num = context.user_data['video_pending']
            user_data[user_id].module_content[module_num] = user_data[user_id].module_content[module_num].replace(
                'Link: https://youtube.com/...', 
                f'Link: {message_text}'
            )
            del context.user_data['video_pending']
            
            # Now proceed with the assessment
            context.user_data['current_module'] = module_num
            module_content = user_data[user_id].module_content.get(module_num, "")
            
            # Extract description to understand the topics
            desc_match = re.search(r'📝 Description:(.*?)(?=\n\n|$)', module_content, re.DOTALL)
            description = desc_match.group(1).strip() if desc_match else ""
            
            # Generate question based on the module topics
            prompt = f"""Generate a conceptual question for Module {module_num} of {user_data[user_id].topic}.

            Module Description:
            {description}

            The user has watched a video about these concepts. Generate a question that:
            1. Tests understanding of the core concepts covered in this module
            2. Requires critical thinking and application of knowledge
            3. Is NOT specific to the video content, but rather about the general concepts
            4. Encourages problem-solving and creative thinking
            5. Could be answered by anyone who understands these concepts well

            Format: Create a clear, challenging question that tests conceptual understanding rather than specific video details."""
            
            try:
                question = model.generate_content(prompt)
                context.user_data['current_question'] = question.text
                
                await update.message.reply_text(
                    f"📝 Conceptual Assessment for Module {module_num}:\n\n"
                    f"{question.text}\n\n"
                    "Please provide your answer in detail, showing your understanding of the concepts."
                )
                return ASSESSMENT
            except Exception as e:
                logger.error(f"Error generating question: {str(e)}")
                await update.message.reply_text(
                    "Sorry, I had trouble generating a question. Please try again."
                )
                return LEARNING
        else:
            await update.message.reply_text(
                "Please provide a valid YouTube video link that you watched for this module.\n"
                "The link should start with 'http' or 'https'."
            )
            return LEARNING
    
    # Check if the message matches "completed module X" pattern
    module_match = re.match(r'completed module (\d+)', message_text)
    
    if module_match:
        completed_module = int(module_match.group(1))
        if 1 <= completed_module <= 5:  # We have 5 modules
            if completed_module != user_data[user_id].current_module:
                await update.message.reply_text(
                    f"Please complete the modules in order. You are currently on Module {user_data[user_id].current_module}."
                )
                return LEARNING
            
            # Add reminder about course completion if this is the last module
            if completed_module == 5:
                context.user_data['final_module'] = True
            
            # Ask for the video link first
            context.user_data['video_pending'] = completed_module
            
            message = f"Great! Before we proceed with the assessment for Module {completed_module}, "\
                     "please share the YouTube video link that you watched for this module.\n\n"\
                     "This helps me generate questions specific to the content you learned."
            
            # Add reminder about course completion option for the last module
            if context.user_data.get('final_module'):
                message += "\n\nℹ️ After completing this final module, you can type 'course completed' "\
                          "to get your Course Completion Certificate with a project assessment!"
            
            await update.message.reply_text(message)
            return LEARNING
        else:
            await update.message.reply_text(
                "Invalid module number. Please specify a module between 1 and 5.\n"
                "Example: 'completed module 1'"
            )
            return LEARNING
    else:
        await update.message.reply_text(
            "To mark a module as completed, please type 'completed module X'\n"
            "For example: 'completed module 1'"
        )
        return LEARNING

async def assess_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_answer = update.message.text.lower()
    current_module = context.user_data.get('current_module', 1)
    
    if user_answer == 'retry':
        await update.message.reply_text(
            f"📝 Here's the question again:\n\n"
            f"{context.user_data['current_question']}\n\n"
            "Please provide your answer in detail."
        )
        return ASSESSMENT
    
    if user_answer.startswith('certify'):
        return await handle_certification_request(update, context)
    
    prompt = f"""
    Question: {context.user_data['current_question']}
    User's Answer: {user_answer}
    
    Evaluate if this answer is correct. Respond with only 'CORRECT' or 'INCORRECT' followed by a brief explanation.
    """
    
    evaluation = model.generate_content(prompt)
    is_correct = evaluation.text.upper().startswith('CORRECT')
    
    if is_correct:
        # Mark module as completed and passed
        user_data[user_id].completed_modules[current_module] = True
        # Update the current module number since this one is completed
        user_data[user_id].current_module = current_module + 1
        
        completed_count = len(user_data[user_id].completed_modules)
        certification_options = []
        
        # Single module certification option
        certification_options.append(f"1. Get certified for Module {current_module} (₹99, type 'certify {current_module}')")
        
        # Multiple module certification options
        if completed_count > 1:
            completed_modules = sorted(user_data[user_id].completed_modules.keys())
            # Option for all completed modules
            total_cost = min(199, 99 * completed_count - (completed_count - 1) * 20)  # ₹20 discount per additional module
            certification_options.append(
                f"2. Get certified for all {completed_count} completed modules "
                f"(Modules {', '.join(map(str, completed_modules))}) "
                f"(₹{total_cost}, type 'certify all')"
            )
        
        certification_options.append(f"{len(certification_options) + 1}. Continue to next module (type 'continue')")
        
        await update.message.reply_text(
            f"Congratulations! {evaluation.text}\n\n"
            "Certification Options:\n" +
            "\n".join(certification_options)
        )
        return CERTIFICATION
    else:
        await update.message.reply_text(
            f"{evaluation.text}\n\n"
            "Type 'retry' to try answering the question again, or review the module content and come back later.\n\n"
            f"You can also type 'certify X' to get certified in any previously completed module, or 'certify all' for all completed modules."
        )
        return ASSESSMENT

async def handle_certification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message = update.message.text.lower()
    regular_certification_form = "https://forms.gle/h1PimKbwbafAGux28"
    
    if message == 'certify all':
        completed_modules = sorted(user_data[user_id].completed_modules.keys())
        module_count = len(completed_modules)
        if module_count > 0:
            # Calculate discounted price: ₹99 for first module, -₹20 for each additional, cap at ₹199
            total_cost = min(199, 99 * module_count - (module_count - 1) * 20)
            await update.message.reply_text(
                f"🎉 Great choice! Get certified for all {module_count} completed modules:\n"
                f"Modules completed: {', '.join(map(str, completed_modules))}\n\n"
                f"💫 Bulk certification special offer:\n"
                f"💰 Original cost: ₹{99 * module_count}\n"
                f"💳 Your price: ₹{total_cost}\n"
                f"🎯 Your savings: ₹{(99 * module_count) - total_cost}!\n\n"
                f"📝 Certification Form: {regular_certification_form}\n\n"
                "Type 'continue' when you're ready to proceed!"
            )
    else:
        # Handle individual module certification
        module_match = re.match(r'certify (\d+)', message)
        if module_match:
            module_num = int(module_match.group(1))
            if module_num in user_data[user_id].completed_modules:
                await update.message.reply_text(
                    f"🎉 Get certified for Module {module_num}!\n\n"
                    f"💳 Cost: ₹99\n"
                    f"📝 Certification Form: {regular_certification_form}\n\n"
                    "Type 'continue' when you're ready to proceed!"
                )
            else:
                await update.message.reply_text(
                    f"❌ Module {module_num} is not yet completed or the assessment wasn't passed.\n"
                    "Please complete the module and pass its assessment first."
                )
    
    return LEARNING

async def handle_certification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    choice = update.message.text.lower()
    regular_certification_form = "https://forms.gle/h1PimKbwbafAGux28"
    course_completion_form = "https://forms.gle/2vwrpeRL39F5rS4j8"
    
    if choice == 'continue':
        next_module = user_data[user_id].current_module
        if next_module <= 5:
            await update.message.reply_text(
                f"🚀 Great! Let's continue with Module {next_module}!\n"
                f"Review the module content above and type 'completed module {next_module}' when you finish.\n\n"
                "Remember: You can get certified in any completed module at any time by typing 'certify X'"
            )
            return LEARNING
        else:
            # All modules completed - show all certification options
            completed_modules = sorted(user_data[user_id].completed_modules.keys())
            await update.message.reply_text(
                "🎓 Congratulations! You've completed all modules!\n\n"
                f"Modules completed: {', '.join(map(str, completed_modules))}\n\n"
                "You have these certification options:\n"
                "1. Get certified for individual modules (type 'certify X')\n"
                "2. Get certified for all modules (type 'certify all')\n"
                "3. Get Course Completion Certificate with project (type 'course completed')\n\n"
                "💡 The Course Completion Certificate includes a project assessment.\n"
                "You'll need to complete a project and submit it via GitHub."
            )
            return CERTIFICATION
    elif choice == 'course completed':
        if len(user_data[user_id].completed_modules) == 5:  # All modules must be completed
            topic = user_data[user_id].topic
            
            prompt = f"""Generate a concise but challenging project problem for {topic}.
            Keep the total response under 3000 characters.
            
            The project should test practical implementation while being concise:
            1. Core functionality that demonstrates mastery
            2. Clear but brief requirements
            3. Essential test cases only
            4. Focus on key concepts learned

            Format (be brief but clear):
            🎯 PROJECT: [Short title]
            
            📋 Goal: [One sentence objective]
            
            📝 Must Have:
            • [Core requirement]
            • [Core requirement]
            • [Core requirement]
            
            💡 Example:
            [One clear input/output example]
            
            ⭐ Success Criteria:
            • Working implementation
            • Clean code
            • Good documentation
            • Error handling
            
            📋 Submit:
            1. Upload completed project to GitHub
            2. Type 'done' for the submission form"""
            
            try:
                project = model.generate_content(prompt)
                project_text = project.text
                
                # Ensure the message with formatting stays under 4096 characters
                guidelines = """
When you've completed the project:
1. Upload it to GitHub with:
   • Clear README.md
   • Setup instructions
   • Sample usage
2. Type 'done' to get the submission form link"""

                total_message = f"🎯 Here's your Course Completion Project:\n\n{project_text}\n\n{guidelines}"
                
                if len(total_message) > 4000:
                    # Truncate the project text if needed
                    max_project_length = 3800 - len(guidelines)
                    project_text = project_text[:max_project_length] + "..."
                    total_message = f"🎯 Here's your Course Completion Project:\n\n{project_text}\n\n{guidelines}"
                
                context.user_data['project_assigned'] = True
                await update.message.reply_text(
                    total_message,
                    parse_mode=ParseMode.HTML
                )
                return CERTIFICATION
            except Exception as e:
                logger.error(f"Error generating project: {str(e)}")
                await update.message.reply_text(
                    "Sorry, I had trouble generating the project. Please try again by typing 'course completed'"
                )
                return CERTIFICATION
        else:
            await update.message.reply_text(
                "⚠️ You need to complete all 5 modules before you can get the course completion project.\n"
                f"You have completed {len(user_data[user_id].completed_modules)} out of 5 modules."
            )
            return CERTIFICATION
    elif choice == 'done':
        if context.user_data.get('project_assigned'):
            await update.message.reply_text(
                "🎉 Great! You're ready to submit your project.\n\n"
                "Please fill out this form to get your Course Completion Certificate:\n"
                f"📝 {course_completion_form}\n\n"
                "The form will ask for:\n"
                "• Your course topic\n"
                "• Project description\n"
                "• GitHub repository link\n"
                "• Any additional notes\n\n"
                "Your certificate will be issued after we review your submission!"
            )
            return CERTIFICATION
        else:
            await update.message.reply_text(
                "⚠️ Please get your project requirements first by typing 'course completed'"
            )
            return CERTIFICATION
    elif choice.startswith('certify'):
        return await handle_certification_request(update, context)
    
    return LEARNING

def main():
    # Create application
    application = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()
    
    # Add conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_topic)],
            LEARNING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_learning)],
            ASSESSMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, assess_answer)],
            CERTIFICATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_certification)]
        },
        fallbacks=[CommandHandler('cancel', lambda update, context: ConversationHandler.END)]
    )
    
    # Add handler
    application.add_handler(conv_handler)
    
    # Set up webhook
    port = int(os.getenv('PORT', 8080))
    webhook_url = os.getenv('WEBHOOK_URL')
    if webhook_url:
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            webhook_url=webhook_url
        )
    else:
        # Fallback to polling (for local development)
        application.run_polling()

# Add Flask route for health check
@app.route('/')
def home():
    return 'Bot is running!'

if __name__ == '__main__':
    main()