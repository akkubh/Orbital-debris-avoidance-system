import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns


df = pd.read_csv('manual_cleaned.csv')
print("✅ File loaded successfully!")

# 1. INTERNAL SORTING: cuisines in each row
def clean_and_sort(text):
    if pd.isna(text): return text
    # Split by comma, capitalize, sort alphabetically, and join back
    items = sorted([i.strip().title() for i in text.split(',')])
    return ", ".join(items)

print("Sorting cuisines alphabetically...")
df['cuisines'] = df['cuisines'].apply(clean_and_sort)

# 2. CLEANING: Rates and Cost
# removing /5 from ratings
def fix_rate(x):
    if pd.isna(x) or str(x) in ['NEW', '-']: return np.nan
    return float(str(x).split('/')[0])

df['rate'] = df['rate'].apply(fix_rate)

# Fill missing values with Median (as said in pdf)
df['rate'] = df['rate'].fillna(df['rate'].median())
df['cost_two_people'] = df['cost_two_people'].fillna(df['cost_two_people'].median())



# 3. --- NEW: ANOMALY DEFENSE SECTION ---
print("🛡️ Filtering extreme anomalies and fraud markers...")

# 1. Define logical boundaries for Cost
# Normal range = ₹100 - ₹5000. Removing the ₹99,999 and ₹0.01 errors.
df = df[(df['cost_two_people'] >= 100) & (df['cost_two_people'] <= 10000)]

# 2. Remove rows with missing critical identifiers
# If a restaurant = no phone & no location, then data entry error.
df = df.dropna(subset=['phone', 'location'], how='all')

# 3. Handle Status/Quantity (If these columns exist )
if 'status' in df.columns:
    df = df[df['status'].str.lower() != 'pending']

print(f"✅ Anomaly filtering complete. Rows remaining: {len(df)}")



# 4. DATA VALIDATION: Cuisine Count
df['cuisines_count'] = df['cuisines'].apply(lambda x: len(str(x).split(',')) if pd.notna(x) else 0)


# 5. TASK 3 PREP: Indiranagar Analysis
indira_counts = df[df['location'] == 'Indiranagar']['service_type'].value_counts()
print("\n--- Indiranagar Service Types (Task 3) ---")
print(indira_counts)


# 6. VISUALIZATION: Task 1 - Pricing Sweet Spot
plt.figure(figsize=(10, 6))
sns.regplot(x='cost_two_people', y='rate', data=df, 
            scatter_kws={'alpha':0.5, 'color':'blue'}, 
            line_kws={'color':'red'})
plt.title('Pricing Sweet Spot: Cost vs. Rating Correlation', fontsize=14)
plt.xlabel('Approx Cost for Two People', fontsize=12)
plt.ylabel('Rating (out of 5)', fontsize=12)
plt.grid(True, linestyle='--', alpha=0.6)


# Save the plot 
plt.savefig('pricing_sweet_spot.png')
print("\n🚀 Visualization saved as 'pricing_sweet_spot.png'")


# Save final cleaned dataset
df.to_csv('final_cleaned_data.csv', index=False)
print("✅ Final cleaned dataset saved as 'final_cleaned_data.csv'")