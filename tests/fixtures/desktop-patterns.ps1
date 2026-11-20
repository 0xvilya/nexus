Add-Type -AssemblyName PresentationFramework
$window=New-Object Windows.Window;$window.Title='RainForge pattern fixture';$window.Width=480;$window.Height=600
$panel=New-Object Windows.Controls.StackPanel;$panel.Margin='20'
$check=New-Object Windows.Controls.CheckBox;$check.Content='Enable fixture'
$label=New-Object Windows.Controls.TextBlock;$label.Text='NOT READY'
$check.Add_Checked({$label.Text='PATTERN READY'});$check.Add_Unchecked({$label.Text='NOT READY'})
$list=New-Object Windows.Controls.ListBox;$list.Height=70
foreach($name in @('Alpha','Beta')){$item=New-Object Windows.Controls.ListBoxItem;$item.Content=$name;[void]$list.Items.Add($item)}
$tree=New-Object Windows.Controls.TreeView;$tree.Height=90
$branch=New-Object Windows.Controls.TreeViewItem;$branch.Header='Branch';$leaf=New-Object Windows.Controls.TreeViewItem;$leaf.Header='Leaf';[void]$branch.Items.Add($leaf);[void]$tree.Items.Add($branch)
$scroll=New-Object Windows.Controls.ScrollViewer;$scroll.Height=120;$scroll.VerticalScrollBarVisibility='Auto';[Windows.Automation.AutomationProperties]::SetAutomationId($scroll,'scrollBox')
$content=New-Object Windows.Controls.StackPanel
1..30 | ForEach-Object {$b=New-Object Windows.Controls.Button;$b.Content="Fixture item $_";$b.Height=28;[void]$content.Children.Add($b)}
$scroll.Content=$content
foreach($control in @($check,$label,$list,$tree,$scroll)){[void]$panel.Children.Add($control)}
$window.Content=$panel;[void]$window.ShowDialog()
