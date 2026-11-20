Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName System.Windows.Forms
$screen=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$window=New-Object Windows.Window
$window.Title='Nexus Pets fullscreen test (4 seconds)'
$window.WindowStyle='None';$window.ResizeMode='NoResize';$window.WindowStartupLocation='Manual'
$window.Left=$screen.Left;$window.Top=$screen.Top;$window.Width=$screen.Width;$window.Height=$screen.Height
$window.Topmost=$true;$window.Background='#14241D'
$label=New-Object Windows.Controls.TextBlock
$label.Text='Nexus Pets display test — closing automatically';$label.FontSize=24;$label.Foreground='White'
$label.HorizontalAlignment='Center';$label.VerticalAlignment='Center';$window.Content=$label
$timer=New-Object Windows.Threading.DispatcherTimer;$timer.Interval=[TimeSpan]::FromSeconds(4)
$timer.Add_Tick({$timer.Stop();$window.Close()});$timer.Start()
[void]$window.ShowDialog()
