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
    
    def __init__(self, input_file: str, start_time: Optional['datetime'] = None, end_time: Optional['datetime'] = None):
        """Initialize plotter with input file (bin or csv) and optional time filters."""
        if not PLOTLY_AVAILABLE:
            raise ImportError("plotly and pandas are required for plotting. Install with: pip install -e \".[plotting]\"")
        
        self.input_file = input_file
        self.start_time = start_time
        self.end_time = end_time
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
        
        # Apply time filtering if specified
        if self.start_time or self.end_time:
            from .utils import apply_time_filter
            self.df = apply_time_filter(self.df, self.start_time, self.end_time)
            print(f"Filtered to {len(self.df)} entries")
        
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
    
    def _insert_gaps_for_temporal_breaks(self, df: pd.DataFrame, gap_threshold_minutes: int = 30):
        """Insert NaN values where there are large temporal gaps in the data."""
        if df.empty:
            return df
        
        df_sorted = df.sort_values('timestamp').copy()
        df_with_gaps = []
        
        for i in range(len(df_sorted)):
            df_with_gaps.append(df_sorted.iloc[i])
            
            # Check if there's a gap to the next data point
            if i < len(df_sorted) - 1:
                current_time = df_sorted.iloc[i]['timestamp']
                next_time = df_sorted.iloc[i + 1]['timestamp']
                time_diff = (next_time - current_time).total_seconds() / 60  # minutes
                
                # If gap is larger than threshold, insert NaN row
                if time_diff > gap_threshold_minutes:
                    gap_row = df_sorted.iloc[i].copy()
                    # Set values to NaN except timestamp (which we'll set to just after current)
                    for col in gap_row.index:
                        if col != 'timestamp' and pd.api.types.is_numeric_dtype(df_sorted[col]):
                            gap_row[col] = pd.NA
                    gap_row['timestamp'] = current_time + pd.Timedelta(minutes=1)
                    df_with_gaps.append(gap_row)
        
        return pd.DataFrame(df_with_gaps).reset_index(drop=True)
    
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
                # Insert gaps for temporal breaks within each mode
                mode_data_with_gaps = self._insert_gaps_for_temporal_breaks(mode_data)
                
                fig.add_trace(go.Scatter(
                    x=mode_data_with_gaps['timestamp'],
                    y=mode_data_with_gaps['state_of_charge_percent'],
                    mode='lines+markers',
                    name=mode,
                    line=dict(color=colors.get(mode, '#8c564b')),
                    connectgaps=False  # Show gaps between separate sessions
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
        
        # Insert gaps for temporal breaks
        riding_data = self._insert_gaps_for_temporal_breaks(riding_data)
        
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        
        # Battery current (primary y-axis)
        fig.add_trace(
            go.Scatter(x=riding_data['timestamp'], y=riding_data['battery_current_amps'],
                      name='Battery Current', line=dict(color='blue'), connectgaps=False),
            secondary_y=False,
        )
        
        # Motor current (secondary y-axis)
        if 'motor_current_amps' in riding_data.columns:
            fig.add_trace(
                go.Scatter(x=riding_data['timestamp'], y=riding_data['motor_current_amps'],
                          name='Motor Current', line=dict(color='red'), connectgaps=False),
                secondary_y=True,
            )
        
        fig.update_xaxes(title_text='Time')
        fig.update_yaxes(title_text='Battery Current (A)', secondary_y=False)
        fig.update_yaxes(title_text='Motor Current (A)', secondary_y=True)
        fig.update_layout(title_text='Power Consumption Analysis')
        
        return fig
    
    def plot_thermal_management(self) -> Figure:
        """Plot temperature deltas relative to ambient temperature."""
        riding_data = self.data['mbb_riding']
        
        # Only use riding data to show gaps when motorcycle is off
        temp_data = self._insert_gaps_for_temporal_breaks(riding_data.sort_values('timestamp'))
        
        if temp_data.empty:
            return go.Figure().add_annotation(text="No temperature data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        # Check if ambient temperature is available
        if 'ambient_temp_celsius' not in temp_data.columns:
            return go.Figure().add_annotation(text="No ambient temperature data available for delta calculation",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        fig = go.Figure()
        
        # Add zero line for ambient temperature baseline
        fig.add_hline(y=0, line_dash="dash", line_color="gray", 
                     annotation_text="Ambient Temperature (Baseline)")
        
        # Calculate temperature deltas relative to ambient
        temp_columns = [
            ('motor_temp_celsius', 'Motor Δ Temperature', '#ff7f0e'),
            ('controller_temp_celsius', 'Controller Δ Temperature', '#2ca02c'),
            ('pack_temp_high_celsius', 'Pack High Δ Temperature', '#d62728')
        ]
        
        for col, name, color in temp_columns:
            if col in temp_data.columns:
                # Calculate delta from ambient temperature
                delta_temp = temp_data[col] - temp_data['ambient_temp_celsius']
                
                fig.add_trace(go.Scatter(
                    x=temp_data['timestamp'],
                    y=delta_temp,
                    mode='lines',
                    name=name,
                    line=dict(color=color),
                    connectgaps=False,  # Show gaps when motorcycle is off
                    customdata=list(zip(temp_data[col], temp_data['ambient_temp_celsius'])),
                    hovertemplate='<b>%{fullData.name}</b><br>' +
                                'Time: %{x}<br>' +
                                'Δ Temperature: %{y:.1f}°C<br>' +
                                'Absolute: %{customdata[0]:.1f}°C<br>' +
                                'Ambient: %{customdata[1]:.1f}°C<br>' +
                                '<extra></extra>'
                ))
        
        fig.update_layout(
            title='Thermal Management - Temperature Deltas from Ambient',
            xaxis_title='Time',
            yaxis_title='Temperature Delta (°C above ambient)',
            hovermode='x unified',
            annotations=[
                dict(
                    text="Positive values indicate temperature above ambient<br>Zero line represents ambient temperature",
                    x=0.02, y=0.98,
                    xref="paper", yref="paper",
                    showarrow=False,
                    font=dict(size=10),
                    bgcolor="rgba(255,255,255,0.8)",
                    bordercolor="gray",
                    borderwidth=1
                )
            ]
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
        
        combined_data = self._insert_gaps_for_temporal_breaks(pd.concat(voltage_data).sort_values('timestamp'))
        
        fig = go.Figure()
        
        # Pack voltage
        if 'pack_voltage_volts' in combined_data.columns:
            fig.add_trace(go.Scatter(
                x=combined_data['timestamp'],
                y=combined_data['pack_voltage_volts'],
                mode='lines',
                name='Pack Voltage',
                line=dict(color='blue'),
                connectgaps=False
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
                line=dict(color='red', dash='dash'),
                connectgaps=False
            ))
            
            fig.add_trace(go.Scatter(
                x=combined_data['timestamp'],
                y=voltage_min_v,
                mode='lines',
                name='Min Cell Voltage',
                line=dict(color='orange', dash='dash'),
                fill='tonexty',
                fillcolor='rgba(255,165,0,0.2)',
                connectgaps=False
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
        """Plot charging session analysis including recuperation."""
        charging_data = self.data['charger_charging']
        stopped_data = self.data['charger_stopped']
        riding_data = self.data['mbb_riding']
        
        # Check if we have any relevant data
        has_charging_data = not (charging_data.empty and stopped_data.empty)
        has_recuperation_data = not riding_data.empty and 'battery_current_amps' in riding_data.columns
        
        if not has_charging_data and not has_recuperation_data:
            return go.Figure().add_annotation(text="No charging or recuperation data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        fig = make_subplots(
            rows=4, cols=1,
            subplot_titles=('AC Voltage', 'EVSE Current', 'State of Charge', 'Recuperation (Regen Braking)'),
            vertical_spacing=0.06
        )
        
        # Add charging data if available
        if has_charging_data:
            all_charging = self._insert_gaps_for_temporal_breaks(pd.concat([charging_data, stopped_data]).sort_values('timestamp'))
            
            if 'voltage_ac' in all_charging.columns:
                fig.add_trace(go.Scatter(
                    x=all_charging['timestamp'],
                    y=all_charging['voltage_ac'],
                    mode='lines+markers',
                    name='AC Voltage',
                    line=dict(color='blue'),
                    connectgaps=False
                ), row=1, col=1)
            
            if 'evse_current_amps' in all_charging.columns:
                fig.add_trace(go.Scatter(
                    x=all_charging['timestamp'],
                    y=all_charging['evse_current_amps'],
                    mode='lines+markers',
                    name='EVSE Current',
                    line=dict(color='red'),
                    connectgaps=False
                ), row=2, col=1)
        
        # Add SOC data if available
        soc_charging = self.data['mbb_charging']
        if not soc_charging.empty and 'state_of_charge_percent' in soc_charging.columns:
            fig.add_trace(go.Scatter(
                x=soc_charging['timestamp'],
                y=soc_charging['state_of_charge_percent'],
                mode='lines+markers',
                name='SOC',
                line=dict(color='green'),
                connectgaps=False
            ), row=3, col=1)
        
        # Add recuperation analysis (negative battery current during riding)
        if has_recuperation_data:
            riding_with_gaps = self._insert_gaps_for_temporal_breaks(riding_data)
            
            # Filter for negative battery current (recuperation events)
            recuperation_data = riding_with_gaps[riding_with_gaps['battery_current_amps'] < 0].copy()
            
            if not recuperation_data.empty:
                # Convert negative current to positive for better visualization
                recuperation_data['recuperation_amps'] = -recuperation_data['battery_current_amps']
                
                fig.add_trace(go.Scatter(
                    x=recuperation_data['timestamp'],
                    y=recuperation_data['recuperation_amps'],
                    mode='lines+markers',
                    name='Recuperation Current',
                    line=dict(color='orange'),
                    connectgaps=False,
                    hovertemplate='<b>Recuperation</b><br>' +
                                'Time: %{x}<br>' +
                                'Regen Current: %{y:.1f}A<br>' +
                                '<extra></extra>'
                ), row=4, col=1)
                
                # Add zero line for reference
                if len(recuperation_data) > 0:
                    time_range = [recuperation_data['timestamp'].min(), recuperation_data['timestamp'].max()]
                    fig.add_trace(go.Scatter(
                        x=time_range,
                        y=[0, 0],
                        mode='lines',
                        name='Zero Line',
                        line=dict(color='gray', dash='dash', width=1),
                        showlegend=False,
                        hoverinfo='skip'
                    ), row=4, col=1)
        
        fig.update_layout(title_text='Charging & Recuperation Analysis', height=1000)
        return fig
    
    def plot_cell_balance(self) -> Figure:
        """Plot cell balance health."""
        bms_data = self.data['bms_discharge']
        
        if bms_data.empty or 'voltage_balance' not in bms_data.columns:
            return go.Figure().add_annotation(text="No cell balance data available",
                                            xref="paper", yref="paper", x=0.5, y=0.5)
        
        # Insert gaps for temporal breaks
        bms_data = self._insert_gaps_for_temporal_breaks(bms_data)
        
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=bms_data['timestamp'],
            y=bms_data['voltage_balance'],
            mode='lines+markers',
            name='Voltage Balance',
            line=dict(color='purple'),
            connectgaps=False
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
        
        # Create output directory if it doesn't exist
        import os
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as e:
            print(f"Error creating output directory '{output_dir}': {e}")
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