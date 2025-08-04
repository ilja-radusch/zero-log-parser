"""Interactive plotting module for Zero Motorcycle log data.

Generates interactive plotly visualizations from Zero Motorcycle log data.
Supports both binary (.bin) and CSV input files.
"""

import json
import os
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Union

try:
    import pandas as pd
    import plotly.graph_objects as go
    import plotly.express as px
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
    Figure = go.Figure
except ImportError:
    PLOTLY_AVAILABLE = False
    pd = None
    go = None
    px = None
    # Mock Figure class for type hints
    class Figure:
        pass

from .core import parse_log


class ZeroLogPlotter:
    """Generate interactive plots from Zero Motorcycle log data."""
    
    def __init__(self, input_file: str):
        """Initialize plotter with input file (bin or csv)."""
        if not PLOTLY_AVAILABLE:
            raise ImportError("plotly and pandas are required for plotting. Install with: pip install -e \".[plotting]\"")
        
        self.input_file = input_file
        self.data = {}
        self.file_type = self._detect_file_type()
        self._load_data()
    
    def _detect_file_type(self) -> str:
        """Detect if input is binary or CSV file."""
        if self.input_file.endswith('.bin'):
            return 'binary'
        elif self.input_file.endswith('.csv'):
            return 'csv'
        else:
            raise ValueError("Input file must be .bin or .csv")
    
    def _load_data(self):
        """Load and parse data from input file."""
        if self.file_type == 'binary':
            self._load_from_binary()
        else:
            self._load_from_csv()
    
    def _load_from_binary(self):
        """Load data from binary file by converting to CSV first."""
        # Create temporary CSV file
        temp_csv = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        temp_csv.close()
        
        # Parse binary to CSV using the core module
        try:
            parse_log(self.input_file, temp_csv.name, output_format='csv')
            self.csv_file = temp_csv.name
            self._load_from_csv()
        finally:
            # Clean up temporary file
            if os.path.exists(temp_csv.name):
                os.unlink(temp_csv.name)
    
    def _load_from_csv(self):
        """Load data from CSV file."""
        csv_file = self.csv_file if hasattr(self, 'csv_file') else self.input_file
        df = pd.read_csv(csv_file, sep=';')
        
        # Parse timestamp column
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Parse JSON conditions into separate columns
        df_expanded = []
        for _, row in df.iterrows():
            row_dict = row.to_dict()
            if pd.notna(row['conditions']) and row['conditions'].strip():
                try:
                    conditions = json.loads(row['conditions'])
                    row_dict.update(conditions)
                except (json.JSONDecodeError, ValueError):
                    pass
            df_expanded.append(row_dict)
        
        self.df = pd.DataFrame(df_expanded)
        
        # Separate by message type for easier access
        self.data = {
            'bms_discharge': self.df[self.df['message'] == 'Discharge level'],
            'bms_soc': self.df[self.df['message'] == 'SOC Data'],
            'mbb_riding': self.df[self.df['message'] == 'Riding'],
            'mbb_disarmed': self.df[self.df['message'] == 'Disarmed'],
            'mbb_charging': self.df[self.df['message'] == 'Charging'],
            'charger_charging': self.df[self.df['message'] == 'Charger 6 Charging'],
            'charger_stopped': self.df[self.df['message'] == 'Charger 6 Stopped'],
        }
    
    def plot_battery_performance(self) -> Figure:
        """Plot battery SOC over time with riding modes."""
        fig = go.Figure()
        
        # Combine all data with SOC
        soc_data = []
        for name, df in self.data.items():
            if not df.empty and 'state_of_charge_percent' in df.columns:
                temp_df = df[['timestamp', 'state_of_charge_percent']].copy()
                temp_df['mode'] = name.replace('_', ' ').title()
                soc_data.append(temp_df)
        
        if soc_data:
            combined_df = pd.concat(soc_data).sort_values('timestamp')
            
            # Color map for different modes
            colors = {
                'Bms Discharge': '#1f77b4',
                'Mbb Riding': '#ff7f0e', 
                'Mbb Disarmed': '#2ca02c',
                'Mbb Charging': '#d62728'
            }
            
            for mode in combined_df['mode'].unique():
                mode_data = combined_df[combined_df['mode'] == mode]
                fig.add_trace(go.Scatter(
                    x=mode_data['timestamp'],
                    y=mode_data['state_of_charge_percent'],
                    mode='lines+markers',
                    name=mode,
                    line=dict(color=colors.get(mode, '#8c564b'))
                ))
        
        fig.update_layout(
            title='Battery State of Charge Over Time',
            xaxis_title='Time',
            yaxis_title='State of Charge (%)',
            hovermode='x unified'
        )
        
        return fig
    
    def plot_power_consumption(self) -> Figure:
        """Plot power consumption during riding."""
        riding_data = self.data['mbb_riding']
        
        if riding_data.empty:
            return go.Figure().add_annotation(text="No riding data available", 
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        
        # Battery current (primary y-axis)
        fig.add_trace(
            go.Scatter(x=riding_data['timestamp'], y=riding_data['battery_current_amps'],
                      name='Battery Current', line=dict(color='blue')),
            secondary_y=False,
        )
        
        # Motor current (secondary y-axis)
        if 'motor_current_amps' in riding_data.columns:
            fig.add_trace(
                go.Scatter(x=riding_data['timestamp'], y=riding_data['motor_current_amps'],
                          name='Motor Current', line=dict(color='red')),
                secondary_y=True,
            )
        
        fig.update_xaxes(title_text='Time')
        fig.update_yaxes(title_text='Battery Current (A)', secondary_y=False)
        fig.update_yaxes(title_text='Motor Current (A)', secondary_y=True)
        fig.update_layout(title_text='Power Consumption Analysis')
        
        return fig
    
    def plot_thermal_management(self) -> Figure:
        """Plot temperature data over time."""
        riding_data = self.data['mbb_riding']
        disarmed_data = self.data['mbb_disarmed']
        
        # Combine riding and disarmed data
        temp_data = pd.concat([riding_data, disarmed_data]).sort_values('timestamp')
        
        if temp_data.empty:
            return go.Figure().add_annotation(text="No temperature data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        fig = go.Figure()
        
        temp_columns = [
            ('motor_temp_celsius', 'Motor Temperature', '#ff7f0e'),
            ('controller_temp_celsius', 'Controller Temperature', '#2ca02c'),
            ('pack_temp_high_celsius', 'Pack High Temperature', '#d62728'),
            ('ambient_temp_celsius', 'Ambient Temperature', '#9467bd')
        ]
        
        for col, name, color in temp_columns:
            if col in temp_data.columns:
                fig.add_trace(go.Scatter(
                    x=temp_data['timestamp'],
                    y=temp_data[col],
                    mode='lines',
                    name=name,
                    line=dict(color=color)
                ))
        
        fig.update_layout(
            title='Thermal Management',
            xaxis_title='Time',
            yaxis_title='Temperature (°C)',
            hovermode='x unified'
        )
        
        return fig
    
    def plot_voltage_analysis(self) -> Figure:
        """Plot voltage analysis over time."""
        bms_data = self.data['bms_soc']
        riding_data = self.data['mbb_riding']
        
        # Combine data sources
        voltage_data = []
        if not bms_data.empty:
            voltage_data.append(bms_data)
        if not riding_data.empty and 'pack_voltage_volts' in riding_data.columns:
            voltage_data.append(riding_data)
        
        if not voltage_data:
            return go.Figure().add_annotation(text="No voltage data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        combined_data = pd.concat(voltage_data).sort_values('timestamp')
        
        fig = go.Figure()
        
        # Pack voltage
        if 'pack_voltage_volts' in combined_data.columns:
            fig.add_trace(go.Scatter(
                x=combined_data['timestamp'],
                y=combined_data['pack_voltage_volts'],
                mode='lines',
                name='Pack Voltage',
                line=dict(color='blue')
            ))
        
        # Min/Max cell voltages from BMS data
        if 'voltage_max' in combined_data.columns and 'voltage_min_1' in combined_data.columns:
            # Convert mV to V
            voltage_max_v = combined_data['voltage_max'] / 1000
            voltage_min_v = combined_data['voltage_min_1'] / 1000
            
            fig.add_trace(go.Scatter(
                x=combined_data['timestamp'],
                y=voltage_max_v,
                mode='lines',
                name='Max Cell Voltage',
                line=dict(color='red', dash='dash')
            ))
            
            fig.add_trace(go.Scatter(
                x=combined_data['timestamp'],
                y=voltage_min_v,
                mode='lines',
                name='Min Cell Voltage',
                line=dict(color='orange', dash='dash'),
                fill='tonexty',
                fillcolor='rgba(255,165,0,0.2)'
            ))
        
        fig.update_layout(
            title='Voltage Analysis',
            xaxis_title='Time',
            yaxis_title='Voltage (V)',
            hovermode='x unified'
        )
        
        return fig
    
    def plot_performance_efficiency(self) -> Figure:
        """Plot performance vs efficiency scatter."""
        riding_data = self.data['mbb_riding']
        
        if riding_data.empty or 'motor_rpm' not in riding_data.columns:
            return go.Figure().add_annotation(text="No performance data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        # Filter out zero RPM for meaningful analysis
        perf_data = riding_data[riding_data['motor_rpm'] > 0]
        
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=perf_data['motor_rpm'],
            y=perf_data['battery_current_amps'],
            mode='markers',
            marker=dict(
                color=perf_data['state_of_charge_percent'],
                colorscale='Viridis',
                colorbar=dict(title='SOC (%)'),
                size=6
            ),
            text=perf_data['state_of_charge_percent'],
            hovertemplate='RPM: %{x}<br>Current: %{y}A<br>SOC: %{text}%<extra></extra>'
        ))
        
        fig.update_layout(
            title='Performance vs Efficiency',
            xaxis_title='Motor RPM',
            yaxis_title='Battery Current (A)',
        )
        
        return fig
    
    def plot_charging_analysis(self) -> Figure:
        """Plot charging session analysis."""
        charging_data = self.data['charger_charging']
        stopped_data = self.data['charger_stopped']
        
        if charging_data.empty and stopped_data.empty:
            return go.Figure().add_annotation(text="No charging data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        fig = make_subplots(
            rows=3, cols=1,
            subplot_titles=('AC Voltage', 'EVSE Current', 'State of Charge'),
            vertical_spacing=0.08
        )
        
        # Combine charging data
        all_charging = pd.concat([charging_data, stopped_data]).sort_values('timestamp')
        
        if 'voltage_ac' in all_charging.columns:
            fig.add_trace(go.Scatter(
                x=all_charging['timestamp'],
                y=all_charging['voltage_ac'],
                mode='lines+markers',
                name='AC Voltage',
                line=dict(color='blue')
            ), row=1, col=1)
        
        if 'evse_current_amps' in all_charging.columns:
            fig.add_trace(go.Scatter(
                x=all_charging['timestamp'],
                y=all_charging['evse_current_amps'],
                mode='lines+markers',
                name='EVSE Current',
                line=dict(color='red')
            ), row=2, col=1)
        
        # Add SOC data if available
        soc_charging = self.data['mbb_charging']
        if not soc_charging.empty and 'state_of_charge_percent' in soc_charging.columns:
            fig.add_trace(go.Scatter(
                x=soc_charging['timestamp'],
                y=soc_charging['state_of_charge_percent'],
                mode='lines+markers',
                name='SOC',
                line=dict(color='green')
            ), row=3, col=1)
        
        fig.update_layout(title_text='Charging Session Analysis', height=800)
        return fig
    
    def plot_cell_balance(self) -> Figure:
        """Plot cell balance health."""
        bms_data = self.data['bms_discharge']
        
        if bms_data.empty or 'voltage_balance' not in bms_data.columns:
            return go.Figure().add_annotation(text="No cell balance data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=bms_data['timestamp'],
            y=bms_data['voltage_balance'],
            mode='lines+markers',
            name='Voltage Balance',
            line=dict(color='purple')
        ))
        
        # Add threshold line (typical good balance is <10mV)
        fig.add_hline(y=10, line_dash="dash", line_color="red", 
                     annotation_text="10mV Threshold")
        
        fig.update_layout(
            title='Cell Balance Health',
            xaxis_title='Time',
            yaxis_title='Voltage Balance (mV)',
        )
        
        return fig
    
    def plot_range_analysis(self) -> Figure:
        """Plot odometer vs SOC for range analysis."""
        riding_data = self.data['mbb_riding']
        
        if riding_data.empty or 'odometer_km' not in riding_data.columns:
            return go.Figure().add_annotation(text="No odometer data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=riding_data['odometer_km'],
            y=riding_data['state_of_charge_percent'],
            mode='markers',
            marker=dict(
                color=riding_data['battery_current_amps'],
                colorscale='RdYlBu_r',
                colorbar=dict(title='Battery Current (A)'),
                size=6
            ),
            text=riding_data['battery_current_amps'],
            hovertemplate='Odometer: %{x} km<br>SOC: %{y}%<br>Current: %{text}A<extra></extra>'
        ))
        
        fig.update_layout(
            title='Range Analysis: Odometer vs SOC',
            xaxis_title='Odometer (km)',
            yaxis_title='State of Charge (%)',
        )
        
        return fig
    
    def generate_all_plots(self, output_dir: str = '.'):
        """Generate all available plots and save as HTML files."""
        if not PLOTLY_AVAILABLE:
            print("Error: plotly is required for plotting. Install with: pip install -e \".[plotting]\"")
            return
        
        plots = {
            'battery_performance': self.plot_battery_performance,
            'power_consumption': self.plot_power_consumption,
            'thermal_management': self.plot_thermal_management,
            'voltage_analysis': self.plot_voltage_analysis,
            'performance_efficiency': self.plot_performance_efficiency,
            'charging_analysis': self.plot_charging_analysis,
            'cell_balance': self.plot_cell_balance,
            'range_analysis': self.plot_range_analysis,
        }
        
        base_name = os.path.splitext(os.path.basename(self.input_file))[0]
        
        for plot_name, plot_func in plots.items():
            try:
                fig = plot_func()
                output_file = os.path.join(output_dir, f"{base_name}_{plot_name}.html")
                fig.write_html(output_file)
                print(f"Generated: {output_file}")
            except Exception as e:
                print(f"Error generating {plot_name}: {e}")