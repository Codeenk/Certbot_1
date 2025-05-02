import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import google.generativeai as genai
import logging

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
TOPIC, LEARNING, ASSESSMENT, CERTIFICATION, FINAL_PROJECT = range(5)

# User session storage
user_data = {}

class CourseModule:
    def __init__(self, topic):
        self.topic = topic
        self.current_module = 1
        self.completed_modules = {}  # Stores module number: passed status
        self.modules = self._generate_modules()
        
    def _generate_modules(self):
        try:
            prompt = f"""Generate a structured learning path for {self.topic} with 5 modules.
For each module, format the output as follows:
📚 Module [NUMBER]: [NAME]
━━━━━━━━━━━━━━━━━━━━━━━━━
📝 Description:
[Clear and concise description]

🎯 Key Topics:
• [Topic 1]
• [Topic 2]
• [Topic 3]

🎬 Recommended Resources:
• Title: [specific video title]
  Link: [actual YouTube educational video link]
  Duration: [approximate duration]

Use proper spacing and emoji to make it visually appealing and easy to read.
Keep descriptions practical and beginner-friendly.
For Python specifically, include actual links to well-known educational channels like:
- Corey Schafer
- Tech With Tim
- freeCodeCamp
- Programming with Mosh

Format everything in markdown for better readability in Telegram.
Keep the total response under 4000 characters."""

            response = model.generate_content(prompt)
            formatted_response = response.text.replace('[', '').replace(']', '')  # Remove square brackets
            
            # Check if response is too long for Telegram
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
            await update.message.reply_text(chunk)
    else:
        await update.message.reply_text(
            f"Great choice! Here's your learning path for {topic}:\n\n"
            f"{modules}\n\n"
            "When you complete a module, type 'completed module X'\n"
            "For example: 'completed module 1'"
        )
    return LEARNING

async def handle_learning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message_text = update.message.text.lower()
    
    # Check if the message matches "completed module X" pattern
    import re
    module_match = re.match(r'completed module (\d+)', message_text)
    
    if module_match:
        completed_module = int(module_match.group(1))
        if 1 <= completed_module <= 5:  # We have 5 modules
            if completed_module != user_data[user_id].current_module:
                await update.message.reply_text(
                    f"Please complete the modules in order. You are currently on Module {user_data[user_id].current_module}."
                )
                return LEARNING
                
            # Store the current module number for assessment
            context.user_data['current_module'] = completed_module
            
            prompt = f"""Generate a challenging question for Module {completed_module} of {user_data[user_id].topic}.
            Focus on the key concepts covered in this specific module.
            Make the question specific and practical.
            Don't provide the answer."""
            
            question = model.generate_content(prompt)
            context.user_data['current_question'] = question.text
            
            await update.message.reply_text(
                f"📝 Assessment for Module {completed_module}:\n\n"
                f"{question.text}\n\n"
                "Please provide your answer in detail."
            )
            return ASSESSMENT
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
    certification_form = "https://forms.gle/h1PimKbwbafAGux28"
    
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
                f"📝 Certification Form: {certification_form}\n\n"
                "Type 'continue' when you're ready to proceed!"
            )
    else:
        # Handle individual module certification
        import re
        module_match = re.match(r'certify (\d+)', message)
        if module_match:
            module_num = int(module_match.group(1))
            if module_num in user_data[user_id].completed_modules:
                await update.message.reply_text(
                    f"🎉 Get certified for Module {module_num}!\n\n"
                    f"💳 Cost: ₹99\n"
                    f"📝 Certification Form: {certification_form}\n\n"
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
    
    if choice == 'continue':
        # No need to calculate next_module, we already updated it in assess_answer
        next_module = user_data[user_id].current_module
        if next_module <= 5:
            await update.message.reply_text(
                f"🚀 Great! Let's continue with Module {next_module}!\n"
                f"Review the module content above and type 'completed module {next_module}' when you finish.\n\n"
                "Remember: You can get certified in any completed module at any time by typing 'certify X'"
            )
            return LEARNING
        else:
            # All modules completed - offer final project
            completed_modules = sorted(user_data[user_id].completed_modules.keys())
            topic = user_data[user_id].topic
            
            prompt = f"""Generate a mini-project problem for someone who has completed a full course in {topic}.
            The project should:
            1. Incorporate concepts from all modules
            2. Be completable in a single code file
            3. Be challenging but doable
            4. Have clear requirements and deliverables
            5. Include sample input/output examples
            
            Format the response as:
            🎯 FINAL PROJECT CHALLENGE
            
            📋 Project Title:
            [Title]
            
            🎯 Objective:
            [Clear 1-2 sentence objective]
            
            📝 Requirements:
            • [Requirement 1]
            • [Requirement 2]
            • ...
            
            ⚡ Key Features to Implement:
            • [Feature 1]
            • [Feature 2]
            • ...
            
            📊 Sample Input/Output:
            [Clear examples]
            
            📌 Important Notes:
            • All code must be in a single file
            • Include comments explaining your code
            • Follow best practices
            
            ⏳ When ready, send your completed code file.
            Type 'submit project' to submit your solution."""
            
            try:
                project = model.generate_content(prompt)
                context.user_data['final_project'] = project.text
                
                await update.message.reply_text(
                    "🎓 Congratulations! You've completed all modules!\n\n"
                    f"Modules completed: {', '.join(map(str, completed_modules))}\n\n"
                    "You have two options:\n"
                    "1. Get certified now (type 'certify all')\n"
                    "2. Take on the final project challenge (type 'start project')\n\n"
                    "💡 Completing the final project will give you an advanced certification!"
                )
            except Exception as e:
                logger.error(f"Error generating final project: {str(e)}")
                await update.message.reply_text(
                    "🎓 Congratulations! You've completed all modules!\n"
                    "You can get certified in individual modules or all modules together.\n"
                    "Type 'certify X' for specific module X, or 'certify all' for all modules."
                )
            return CERTIFICATION
    elif choice == 'start project':
        if 'final_project' in context.user_data:
            await update.message.reply_text(
                f"{context.user_data['final_project']}"
            )
            return FINAL_PROJECT
    elif choice.startswith('certify'):
        return await handle_certification_request(update, context)
    
    return LEARNING

async def handle_final_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message = update.message.text.lower()
    certification_form = "https://forms.gle/h1PimKbwbafAGux28"
    
    if message == 'submit project':
        await update.message.reply_text(
            "🎯 Great! Please send your code file for review.\n"
            "Make sure:\n"
            "• All code is in a single file\n"
            "• Code is well-commented\n"
            "• All requirements are implemented\n\n"
            "Reply with your code file when ready."
        )
        return FINAL_PROJECT
    elif update.message.document:
        # User has sent a file
        file = update.message.document
        if not file.file_name.endswith(('.py', '.ipynb', '.js', '.java', '.cpp', '.c')):
            await update.message.reply_text(
                "❌ Please submit a valid code file (supported formats: .py, .ipynb, .js, .java, .cpp, .c)"
            )
            return FINAL_PROJECT
            
        prompt = f"""Review this project submission for {user_data[user_id].topic}.
        Check if it meets all requirements from the project description.
        Respond with PASS or FAIL followed by a brief explanation."""
        
        # Here you would normally download and process the file
        # For now, we'll simulate a review
        review = model.generate_content(prompt)
        is_passed = review.text.upper().startswith('PASS')
        
        if is_passed:
            await update.message.reply_text(
                f"🎉 Congratulations! Your project has passed the review!\n\n"
                f"{review.text}\n\n"
                f"🏆 You've earned the Advanced Certification!\n"
                f"💳 Cost for Advanced Certification (includes all modules): ₹299\n"
                f"📝 Certification Form: {certification_form}\n\n"
                "This certification validates both your theoretical knowledge and practical skills!"
            )
        else:
            await update.message.reply_text(
                f"📝 Project Review Result:\n\n"
                f"{review.text}\n\n"
                "You can:\n"
                "1. Submit a revised version (send new file)\n"
                "2. Get regular certification instead (type 'certify all')\n"
                "3. Try again later (type 'exit')"
            )
        return FINAL_PROJECT
    
    return LEARNING

def main():
    application = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_topic)],
            LEARNING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_learning)],
            ASSESSMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, assess_answer)],
            CERTIFICATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_certification)],
            FINAL_PROJECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_final_project),
                MessageHandler(filters.Document.ALL, handle_final_project)
            ]
        },
        fallbacks=[]
    )
    
    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == '__main__':
    main()