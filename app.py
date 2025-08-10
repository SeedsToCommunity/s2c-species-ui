import os
import pandas as pd
import logging
import json
from flask import Flask, render_template, flash

# Configure logging for debugging
logging.basicConfig(level=logging.DEBUG)

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

def load_display_config():
    """Load display configuration from JSON file"""
    try:
        with open('config/display_columns.json', 'r') as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        app.logger.error("Display configuration file not found")
        return {"main_page_columns": [], "filter_columns": []}
    except Exception as e:
        app.logger.error(f"Error loading display configuration: {str(e)}")
        return {"main_page_columns": [], "filter_columns": []}

def load_plant_data():
    """Load and process plant data from the tab-separated file"""
    try:
        # Read the tab-separated file
        df = pd.read_csv('origdata.tabsv', sep='\t')
        
        # Clean up column names - replace spaces with underscores and make lowercase
        df.columns = df.columns.str.replace(' ', '_').str.lower()
        
        # Clean up the data - fill NaN values with empty strings
        df = df.fillna('')
        
        # Save as CSV for future reference
        df.to_csv('plants.csv', index=False)
        
        return df
        
    except FileNotFoundError:
        app.logger.error("origdata.tabsv file not found, trying plants.csv")
        # Fallback to existing CSV if tab file doesn't exist
        try:
            df = pd.read_csv('plants.csv')
            df = df.fillna('')
            return df
        except FileNotFoundError:
            app.logger.error("No data files found")
            return pd.DataFrame()
            
    except Exception as e:
        app.logger.error(f"Error reading plant data: {str(e)}")
        return pd.DataFrame()

@app.route('/')
def index():
    """Main route to display plant species list"""
    try:
        # Load plant data from tab-separated file
        df = load_plant_data()
        
        if df.empty:
            flash('Error: No plant data could be loaded. Please check data files.', 'error')
            return render_template('index.html', plants=[], config={})
        
        # Load display configuration
        config = load_display_config()
        
        # Convert to list of dictionaries for easier template rendering
        plants = df.to_dict('records')
        
        app.logger.info(f"Successfully loaded {len(plants)} plant species")
        return render_template('index.html', plants=plants, config=config)
        
    except Exception as e:
        app.logger.error(f"Error processing plant data: {str(e)}")
        flash(f'Error loading plant data: {str(e)}', 'error')
        return render_template('index.html', plants=[], config={})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
