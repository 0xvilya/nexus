Add-Type -AssemblyName PresentationFramework
$window=New-Object Windows.Window;$window.Title='RainForge UIA fixture';$window.Width=450;$window.Height=220
$panel=New-Object Windows.Controls.StackPanel;$panel.Margin='20'
$box=New-Object Windows.Controls.TextBox;[Windows.Automation.AutomationProperties]::SetAutomationId($box,'nameBox')
$button=New-Object Windows.Controls.Button;$button.Content='Apply';[Windows.Automation.AutomationProperties]::SetAutomationId($button,'applyButton')
$label=New-Object Windows.Controls.TextBlock;$label.Text='Waiting'
$box.Add_TextChanged({if($box.Text -eq 'RENAME_TO_SEND'){$button.Content='Send'}})
$button.Add_Click({$label.Text='Hello '+$box.Text})
[void]$panel.Children.Add($box);[void]$panel.Children.Add($button);[void]$panel.Children.Add($label);$window.Content=$panel;[void]$window.ShowDialog()
