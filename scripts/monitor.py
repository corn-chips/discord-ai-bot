#!/usr/bin/env python3
"""
Basic monitoring script for Discord Grok Bot.

Provides simple metrics and status monitoring.
"""

import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


class BotMonitor:
    """Simple monitoring for the Discord Grok Bot."""
    
    def __init__(self, log_file: str = "bot.log"):
        self.log_file = Path(log_file)
        self.logger = logging.getLogger(__name__)
        
    def parse_log_entries(self, since_hours: int = 1) -> List[Dict]:
        """Parse log entries from the specified time period."""
        if not self.log_file.exists():
            return []
        
        entries = []
        cutoff_time = datetime.now() - timedelta(hours=since_hours)
        
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    # Parse timestamp from log line
                    timestamp_match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})', line)
                    if timestamp_match:
                        timestamp_str = timestamp_match.group(1)
                        try:
                            timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                            if timestamp >= cutoff_time:
                                entries.append({
                                    'timestamp': timestamp,
                                    'line': line
                                })
                        except ValueError:
                            continue
                            
        except Exception as e:
            self.logger.error(f"Error parsing log file: {e}")
            
        return entries
    
    def get_error_count(self, entries: List[Dict]) -> int:
        """Count error entries in the logs."""
        return sum(1 for entry in entries if 'ERROR' in entry['line'])
    
    def get_warning_count(self, entries: List[Dict]) -> int:
        """Count warning entries in the logs."""
        return sum(1 for entry in entries if 'WARNING' in entry['line'])
    
    def get_message_count(self, entries: List[Dict]) -> int:
        """Count processed messages."""
        return sum(1 for entry in entries if 'Bot mentioned by' in entry['line'])
    
    def get_response_count(self, entries: List[Dict]) -> int:
        """Count successful responses."""
        return sum(1 for entry in entries if 'Successfully sent response' in entry['line'])
    
    def get_connection_status(self, entries: List[Dict]) -> str:
        """Get the latest connection status."""
        for entry in reversed(entries):
            if 'has connected to Discord' in entry['line']:
                return "Connected"
            elif 'Bot disconnected' in entry['line']:
                return "Disconnected"
        return "Unknown"
    
    def get_uptime_info(self, entries: List[Dict]) -> Optional[str]:
        """Get bot uptime information."""
        for entry in entries:
            if 'has connected to Discord' in entry['line']:
                start_time = entry['timestamp']
                uptime = datetime.now() - start_time
                return str(uptime).split('.')[0]  # Remove microseconds
        return None
    
    def generate_report(self, hours: int = 1) -> Dict:
        """Generate a monitoring report."""
        entries = self.parse_log_entries(hours)
        
        return {
            'timestamp': datetime.now().isoformat(),
            'period_hours': hours,
            'total_log_entries': len(entries),
            'connection_status': self.get_connection_status(entries),
            'uptime': self.get_uptime_info(entries),
            'messages_processed': self.get_message_count(entries),
            'responses_sent': self.get_response_count(entries),
            'error_count': self.get_error_count(entries),
            'warning_count': self.get_warning_count(entries),
            'log_file_size': self.log_file.stat().st_size if self.log_file.exists() else 0,
            'log_file_modified': datetime.fromtimestamp(
                self.log_file.stat().st_mtime
            ).isoformat() if self.log_file.exists() else None
        }
    
    def print_report(self, hours: int = 1):
        """Print a formatted monitoring report."""
        report = self.generate_report(hours)
        
        print(f"📊 Discord Grok Bot Monitor Report")
        print(f"Generated: {report['timestamp']}")
        print(f"Period: Last {hours} hour(s)")
        print("=" * 50)
        
        print(f"🔌 Connection Status: {report['connection_status']}")
        if report['uptime']:
            print(f"⏱️  Uptime: {report['uptime']}")
        
        print(f"📨 Messages Processed: {report['messages_processed']}")
        print(f"✅ Responses Sent: {report['responses_sent']}")
        
        if report['messages_processed'] > 0:
            success_rate = (report['responses_sent'] / report['messages_processed']) * 100
            print(f"📈 Success Rate: {success_rate:.1f}%")
        
        print(f"⚠️  Warnings: {report['warning_count']}")
        print(f"❌ Errors: {report['error_count']}")
        
        print(f"📄 Log Entries: {report['total_log_entries']}")
        print(f"💾 Log File Size: {report['log_file_size']} bytes")
        
        if report['log_file_modified']:
            print(f"📝 Last Log Update: {report['log_file_modified']}")


def main():
    """Main monitoring entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Monitor Discord Grok Bot')
    parser.add_argument('--hours', type=int, default=1, 
                       help='Hours to look back for monitoring (default: 1)')
    parser.add_argument('--json', action='store_true',
                       help='Output report as JSON')
    parser.add_argument('--log-file', default='bot.log',
                       help='Path to log file (default: bot.log)')
    
    args = parser.parse_args()
    
    monitor = BotMonitor(args.log_file)
    
    if args.json:
        report = monitor.generate_report(args.hours)
        print(json.dumps(report, indent=2))
    else:
        monitor.print_report(args.hours)


if __name__ == "__main__":
    main()