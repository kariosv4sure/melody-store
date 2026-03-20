// Dashboard specific functions
$(document).ready(function() {
    // Animate stats numbers
    $('.stat-info h3').each(function() {
        const $this = $(this);
        const value = parseInt($this.text().replace(/[^0-9]/g, ''));
        if (!isNaN(value) && value > 0) {
            let current = 0;
            const increment = Math.ceil(value / 50);
            const timer = setInterval(function() {
                current += increment;
                if (current >= value) {
                    current = value;
                    clearInterval(timer);
                }
                $this.text(current.toLocaleString());
            }, 20);
        }
    });
    
    // Initialize tooltips
    $('[title]').tooltip();
    
    // Add hover effect to action buttons
    $('.action-btn').hover(
        function() {
            $(this).find('i').addClass('fa-bounce');
        },
        function() {
            $(this).find('i').removeClass('fa-bounce');
        }
    );
});

// Format currency function (make sure it's available globally)
function formatCurrency(amount) {
    return '₦' + parseFloat(amount).toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

// Admin Panel JavaScript

$(document).ready(function() {
    // Mobile sidebar toggle
    $('.mobile-menu-toggle').click(function() {
        $('.admin-sidebar').toggleClass('active');
    });
    
    // Auto-hide alerts after 5 seconds
    setTimeout(function() {
        $('.alert').fadeOut('slow');
    }, 5000);
    
    // Delete confirmation
    $('.btn-delete, .delete-btn').click(function(e) {
        if (!confirm('Are you sure you want to delete this item? This action cannot be undone.')) {
            e.preventDefault();
            return false;
        }
    });
    
    // Bulk upload preview
    $('#accounts-text').on('input', function() {
        const lines = $(this).val().split('\n').filter(l => l.trim());
        $('#preview-count').text(lines.length);
        
        if (lines.length > 0) {
            let preview = '<div class="preview-box"><h4>Preview (first 5 lines):</h4><ul>';
            lines.slice(0, 5).forEach(line => {
                const parts = line.split('|');
                preview += `<li>Email: ${parts[0] || '?'} | Password: ${parts[1] || '?'}</li>`;
            });
            preview += '</ul></div>';
            $('#preview-area').html(preview);
        } else {
            $('#preview-area').empty();
        }
    });
    
    // Product features formatting helper
    $('#features').on('input', function() {
        const features = $(this).val().split('\n').filter(f => f.trim());
        $('#features-preview').html(`<strong>${features.length}</strong> features added`);
    });
    
    // Quick stock check
    function checkLowStock() {
        $('.stock-badge').each(function() {
            const stock = parseInt($(this).data('stock'));
            if (stock <= 3 && stock > 0) {
                $(this).addClass('low-stock').text(`⚠️ Only ${stock} left`);
            } else if (stock === 0) {
                $(this).addClass('out-of-stock').text('Out of Stock');
            }
        });
    }
    
    checkLowStock();
});

// Copy to clipboard function
function copyToClipboard(text, elementId) {
    navigator.clipboard.writeText(text).then(function() {
        const originalText = $(`#${elementId}`).html();
        $(`#${elementId}`).html('<i class="fas fa-check"></i> Copied!');
        setTimeout(() => {
            $(`#${elementId}`).html(originalText);
        }, 2000);
    });
}

// Format currency
function formatCurrency(amount) {
    return '₦' + amount.toLocaleString();
}

// Filter table rows
function filterTable(inputId, tableId) {
    const input = document.getElementById(inputId);
    const filter = input.value.toUpperCase();
    const table = document.getElementById(tableId);
    const rows = table.getElementsByTagName('tr');
    
    for (let i = 1; i < rows.length; i++) {
        let text = '';
        const cells = rows[i].getElementsByTagName('td');
        for (let j = 0; j < cells.length; j++) {
            text += cells[j].textContent || cells[j].innerText;
        }
        if (text.toUpperCase().indexOf(filter) > -1) {
            rows[i].style.display = '';
        } else {
            rows[i].style.display = 'none';
        }
    }
}

// Confirm action
function confirmAction(message) {
    return confirm(message);
}
