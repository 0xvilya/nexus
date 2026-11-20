Add-Type -AssemblyName PresentationFramework
$window=New-Object Windows.Window;$window.Title='Nexus owned screen reading fixture';$window.Width=1000;$window.Height=550
$panel=New-Object Windows.Controls.StackPanel;$panel.Margin='25'
$label=New-Object Windows.Controls.TextBlock;$label.Text='English reading exercise';$label.FontSize=28
$input=New-Object Windows.Controls.TextBox;$input.Text='English answer: Cambridge';$input.FontSize=22
[Windows.Automation.AutomationProperties]::SetName($input,'Answer field')
$secret=New-Object Windows.Controls.PasswordBox;$secret.Password='EXCLUDED_SECRET_246'
[Windows.Automation.AutomationProperties]::SetAutomationId($secret,'privateInput')
$footer=New-Object Windows.Controls.TextBlock;$footer.Text='Only this owned test window is captured.';$footer.FontSize=18
[void]$panel.Children.Add($label);[void]$panel.Children.Add($input);[void]$panel.Children.Add($secret);[void]$panel.Children.Add($footer)
$window.Content=$panel;[void]$window.ShowDialog()
