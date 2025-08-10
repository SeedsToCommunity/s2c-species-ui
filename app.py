import os
import pandas as pd
import logging
from flask import Flask, render_template, flash

# Configure logging for debugging
logging.basicConfig(level=logging.DEBUG)

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

@app.route('/')
def index():
    """Main route to display plant species list"""
    try:
        # Read the CSV file using pandas
        df = pd.read_csv('plants.csv')
        
        # Convert to list of dictionaries for easier template rendering
        plants = df.to_dict('records')
        
        app.logger.info(f"Successfully loaded {len(plants)} plant species")
        return render_template('index.html', plants=plants)
        
    except FileNotFoundError:
        app.logger.error("plants.csv file not found")
        flash('Error: Plant data file not found. Please ensure plants.csv exists.', 'error')
        return render_template('index.html', plants=[])
        
    except pd.errors.EmptyDataError:
        app.logger.error("plants.csv file is empty")
        flash('Error: Plant data file is empty.', 'error')
        return render_template('index.html', plants=[])
        
    except Exception as e:
        app.logger.error(f"Error reading plant data: {str(e)}")
        flash(f'Error loading plant data: {str(e)}', 'error')
        return render_template('index.html', plants=[])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
