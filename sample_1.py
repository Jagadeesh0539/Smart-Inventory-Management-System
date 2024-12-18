from flask import Flask, request, jsonify, render_template, redirect, url_for
import joblib
import pandas as pd
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Load the saved model
model = joblib.load('model/demand_model.pkl')

# Set up file upload folder
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['ALLOWED_EXTENSIONS'] = {'csv'}

# Check if file is an allowed type (CSV)
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Route for the home page
@app.route('/')
def home():
    return render_template('index.html')

# Route to handle file upload and prediction
@app.route('/upload', methods=['POST'])
def upload_file():
    # Check if a file is uploaded
    if 'file' not in request.files:
        return "No file part"
    
    file = request.files['file']
    
    # If no file is selected
    if file.filename == '':
        return "No selected file"
    
    if file and allowed_file(file.filename):
        # Save the uploaded file
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Load the uploaded CSV file into a DataFrame
        input_data = pd.read_csv(filepath)

        # Process the CSV by dropping 'ITEM_NAME' and predicting
        input_features = input_data[['ROLLING_MEAN', 'ROLLING_SD', 'LAG_1', 'LAG_2']]
        predictions = model.predict(input_features)

        # Categorize predictions into LOW, MEDIUM, HIGH
        demand_category = pd.qcut(predictions, q=3, labels=["LOW", "MEDIUM", "HIGH"])

        # Add predictions and categories back to the DataFrame
        input_data['Predicted_Quantity'] = predictions
        input_data['DEMAND'] = demand_category

        # Convert the DataFrame to HTML for display
        result_table = input_data.to_html(classes='table table-striped', index=False)

        return render_template('result.html', tables=[result_table], titles=[''])

    return "File type not allowed"

if __name__ == '__main__':
    app.run(debug=True)
