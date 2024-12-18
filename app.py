from flask import Flask, render_template, request, flash, redirect, url_for, session , jsonify , send_file
import os
import joblib
import numpy as np
import pandas as pd
from werkzeug.utils import secure_filename
from pymongo import MongoClient
import smtplib
from flask_mail import Mail, Message

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Replace with a strong secret key
client = MongoClient('localhost', 27017)
db = client['DemandPrediction']  # Replace with your MongoDB database name
users_collection = db['users']

# Load the demand and discount models
demand_model = joblib.load('model/demand_model.pkl')
discount_model = joblib.load('model/discount_model.pkl')



app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USERNAME'] = 'jagadeeshkumar0539@gmail.com'  # Use your actual Gmail address
app.config['MAIL_PASSWORD'] = 'gqmb xnnr silp tvui'     # Use your generated App Password
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
mail = Mail(app)

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['ALLOWED_EXTENSIONS'] = {'csv'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/predict')
def predict():
    if not session.get('logged_in'):
        flash('You need to log in first.', 'warning')
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "No file part in the request", 400

    file = request.files['file']

    if file.filename == '':
        return "No selected file", 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Read and process the uploaded data
        input_data = pd.read_csv(filepath)
        
        # Ensure necessary columns are present
        required_columns = ['ITEM_NAME', 'ROLLING_MEAN', 'ROLLING_SD', 'LAG_1', 'LAG_2', 'QUANTITY', 'SHIP_DATE', 'EXPIRY_DATE', 'SHELL_LIFE']
        if not all(col in input_data.columns for col in required_columns):
            return "Uploaded file is missing one or more required columns", 400

        # Fill missing values in specific columns
        numerical_cols = ['ROLLING_MEAN', 'ROLLING_SD', 'LAG_1', 'LAG_2']
        input_data[numerical_cols] = input_data[numerical_cols].fillna(input_data[numerical_cols].mean())

        # Define features and target
        X = input_data[['ITEM_NAME', 'ROLLING_MEAN', 'ROLLING_SD', 'LAG_1', 'LAG_2']]
        y = input_data['QUANTITY']

        # Remove 'ITEM_NAME' for prediction
        X_features = X.drop('ITEM_NAME', axis=1)

        # Make predictions
        predictions = demand_model.predict(X_features)
        demand_category = pd.qcut(predictions, q=3, labels=["LOW", "MEDIUM", "HIGH"])

        # Create DataFrame for results
        results = pd.DataFrame({
            'ITEM_NAME': X['ITEM_NAME'].reset_index(drop=True),
            'Actual_Quantity': y.reset_index(drop=True),
            'Predicted_Quantity': predictions,
            'DEMAND': demand_category
        })

        # Aggregate results
        summary_results = results.groupby('ITEM_NAME').agg({
            'Actual_Quantity': 'mean',  
            'Predicted_Quantity': 'mean',  
            'DEMAND': lambda x: x.mode()[0]  
        }).reset_index()

        # Select and merge relevant columns, including SHELL_LIFE
        columns_to_include = ['ITEM_NAME', 'SHIP_DATE', 'EXPIRY_DATE', 'SHELL_LIFE','PRICE']
        data_subset = input_data[columns_to_include].drop_duplicates(subset='ITEM_NAME')
        aggregated_demand_summary = pd.merge(data_subset, summary_results, on='ITEM_NAME', how='left')

        # Save final summary to CSV
        output_filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'aggregated_demand_summary.csv')
        aggregated_demand_summary.to_csv(output_filepath, index=False)

        # Convert summary to HTML table
        result_table = aggregated_demand_summary.to_html(classes='table table-striped', index=False)

        # Render results in HTML template
        return render_template('result.html', tables=[result_table], titles=['Aggregated Demand Summary'])

    return "File type not allowed", 400

def calculate_discount(row):
    if row['DAYS_TO_EXPIRY'] >= 30 and row['SHELL_LIFE'] < 365:
        return 0
    if row['IS_PERISHABLE'] == 1:
        if row['DEMAND'] == 'HIGH':
            if row['DAYS_TO_EXPIRY'] < 10:
                return 20
            elif row['DAYS_TO_EXPIRY'] < 20:
                return 10
            elif row['DAYS_TO_EXPIRY'] < 30:
                return 5
        elif row['DEMAND'] == 'MEDIUM':
            if row['DAYS_TO_EXPIRY'] < 10:
                return 50
            elif row['DAYS_TO_EXPIRY'] < 20:
                return 30
            elif row['DAYS_TO_EXPIRY'] < 30:
                return 25
        elif row['DEMAND'] == 'LOW':
            if row['DAYS_TO_EXPIRY'] < 10:
                return 60
            elif row['DAYS_TO_EXPIRY'] < 20:
                return 45
            elif row['DAYS_TO_EXPIRY'] < 30:
                return 35
    else:
        if row['DEMAND'] == 'HIGH':
            if row['SHELL_LIFE'] > 500:
                return 10
            elif row['SHELL_LIFE'] > 400:
                return 5
            elif row['SHELL_LIFE'] > 365:
                return 95
        elif row['DEMAND'] == 'MEDIUM':
            if row['SHELL_LIFE'] > 500:
                return 35
            elif row['SHELL_LIFE'] > 400:
                return 25
            elif row['SHELL_LIFE'] > 365:
                return 10
        elif row['DEMAND'] == 'LOW':
            if row['SHELL_LIFE'] > 500:
                return 50
            elif row['SHELL_LIFE'] > 400:
                return 40
            elif row['SHELL_LIFE'] > 365:
                return 30
    return 0


@app.route('/discount', methods=['GET'])
def discount():
    try:
        # Load the aggregated demand summary
        aggregated_filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'aggregated_demand_summary.csv')
        aggregated_data = pd.read_csv(aggregated_filepath)

        # Add perishable and date-related columns
        aggregated_data['IS_PERISHABLE'] = np.where(aggregated_data['EXPIRY_DATE'].notna(), 1, 0)
        aggregated_data['EXPIRY_DATE'] = pd.to_datetime(aggregated_data['EXPIRY_DATE'], errors='coerce')
        aggregated_data['CURRENT_DATE'] = pd.to_datetime('today')
        aggregated_data['DAYS_TO_EXPIRY'] = np.where(
            aggregated_data['EXPIRY_DATE'].notna(),
            (aggregated_data['EXPIRY_DATE'] - aggregated_data['CURRENT_DATE']).dt.days,
            9999
        )

        # Prepare features for discount prediction
        aggregated_data['DISCOUNT'] = aggregated_data.apply(calculate_discount, axis=1)
        aggregated_data['DISCOUNT'] = aggregated_data['DISCOUNT'].astype(str) + '%'
        aggregated_data['DEMAND'] = aggregated_data['DEMAND'].map({'LOW': 1, 'MEDIUM': 2, 'HIGH': 3})

        discount_features = aggregated_data[
            ['DEMAND', 'IS_PERISHABLE', 'DAYS_TO_EXPIRY', 'SHELL_LIFE', 'Actual_Quantity', 'Predicted_Quantity']]

        # Predict discounts
        aggregated_data['Predicted_Discount'] = discount_model.predict(discount_features)
        aggregated_data['Predicted_Discount'] = aggregated_data['Predicted_Discount'].round()
        # Calculate new prices based on discount
        aggregated_data['PRICE'] = pd.to_numeric(aggregated_data['PRICE'], errors='coerce')
        aggregated_data['newPrice'] = aggregated_data.apply(
            lambda row: row['PRICE'] * (1 - row['Predicted_Discount'] / 100) if not pd.isna(row['PRICE']) else None,
            axis=1
        )

        # Filter out rows with 0% discount
        aggregated_data = aggregated_data[aggregated_data['DISCOUNT'] != '0%']
        aggregated_data = aggregated_data.drop(['Actual_Quantity', 'Predicted_Quantity'], axis=1)
        # Convert results to HTML table
        discount_table = aggregated_data.to_html(classes='table table-striped', index=False, escape=False)
        return render_template('discount_results.html', tables=[discount_table], titles=[''])

    except FileNotFoundError:
        flash('Please upload the demand file first.', 'warning')
        return redirect(url_for('predict'))



@app.route('/send_email', methods=['GET'])
def send_email():
    if 'username' not in session:
        return jsonify({"error": "User not logged in!"}), 403
    
    recipient_email = session['username']  # Use the logged-in user's email
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'aggregated_demand_summary.csv')
    
    if not os.path.exists(file_path):
        return jsonify({"error": "File not found!"}), 404

    # Configure email
    msg = Message(
        subject='Aggregated Demand Summary',
        sender=app.config['MAIL_USERNAME'],
        recipients=[recipient_email]
    )
    msg.body = "Please find the attached aggregated demand summary report."

    # Attach file
    with app.open_resource(file_path) as file:
        msg.attach("aggregated_demand_summary.csv", "text/csv", file.read())

    # Send email
    try:
        mail.send(msg)
        return jsonify({"message": "Message sent with attachment!"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to send message: {str(e)}"}), 500


@app.route('/download_summary')
def download_summary():
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'aggregated_demand_summary.csv')
    return send_file(filepath, as_attachment=True)


@app.route('/ModelDetails')
def ModelResults():
    return render_template('ModelDetails.html')




@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = users_collection.find_one({'username': username, 'password': password})
        if user:
            session['logged_in'] = True
            session['username'] = username
            flash('Login successful.', 'success')
            return redirect(url_for('predict'))
        else:
            flash('Invalid username or password. Please try again.', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if users_collection.find_one({'username': username}):
            flash('Username already exists. Choose a different one.', 'danger')
        else:
            users_collection.insert_one({'username': username, 'password': password})
            flash('Registration successful. You can now log in.', 'success')
            return redirect(url_for('login'))

    return render_template('register.html')

if __name__ == '__main__':
    app.run(debug=True)
